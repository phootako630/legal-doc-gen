# OCR 并发隔离：全局名额上限、不挤占默认线程池（登录校验等）、排队不算超时、解析不卡事件循环。
# DashScope 请求一律打桩成同步 sleep（与 requests 一样阻塞线程），不触网。
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import fitz

from app.services import file_parser, ocr_engine


def _pdf(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _fake_ocr(monkeypatch, seconds: float):
    """打桩 DashScope 调用，记录同时在识别的最大页数。"""
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake(_img):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(seconds)
        with lock:
            state["now"] -= 1
        return "识别文字"

    monkeypatch.setattr(ocr_engine, "_ocr_image_b64", fake)
    monkeypatch.setattr(ocr_engine, "_keys", lambda: ["虚构key"])
    return state


def test_global_cap_across_analyses(monkeypatch):
    # 多个分析同时 OCR：全服务同时识别的页数不超过名额
    state = _fake_ocr(monkeypatch, 0.1)
    monkeypatch.setattr(ocr_engine, "OCR_GLOBAL_CONCURRENCY", 3)
    pdf = _pdf(4)

    async def scenario():
        analyses = [
            asyncio.gather(*(ocr_engine.ocr_page(pdf, n) for n in range(1, 5)))
            for _ in range(5)
        ]
        return await asyncio.gather(*analyses)

    results = asyncio.run(scenario())
    assert all(t == "识别文字" for pages in results for t in pages)
    assert state["max"] == 3


def test_ocr_does_not_starve_default_pool(monkeypatch):
    # 登录校验用 asyncio.to_thread（默认线程池）：OCR 把线程全占住时它也不能排队（2 核服务器默认池仅
    # 6 线程）。OCR 打桩成一直卡住，不靠计时判断：若 OCR 占的是默认池，to_thread 会一直等到超时
    release = threading.Event()
    started = threading.Semaphore(0)

    def stuck(_img):
        started.release()
        release.wait(10)
        return "识别文字"

    monkeypatch.setattr(ocr_engine, "_ocr_image_b64", stuck)
    monkeypatch.setattr(ocr_engine, "_keys", lambda: ["虚构key"])
    pdf = _pdf(5)

    async def scenario():
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=2)
        )
        ocr = asyncio.gather(
            *(ocr_engine.ocr_page(pdf, n) for _ in range(4) for n in range(1, 6))
        )
        try:
            # 等至少两页真正卡在识别里（旧实现下默认池的 2 个线程此时已被占满）
            for _ in range(2):
                await asyncio.wait_for(asyncio.to_thread(started.acquire), 5)
            await asyncio.wait_for(asyncio.to_thread(lambda: None), 5)
        finally:
            release.set()
        return await ocr

    assert len(asyncio.run(scenario())) == 20


def test_queued_pages_do_not_time_out(monkeypatch):
    # 名额满时排队等待不计入单页超时：排队的页照样识别成功，不被当成超时跳过
    _fake_ocr(monkeypatch, 0.3)
    monkeypatch.setattr(ocr_engine, "OCR_GLOBAL_CONCURRENCY", 2)
    monkeypatch.setattr(
        ocr_engine, "OCR_PAGE_TIMEOUT", 1.0
    )  # 单页 0.3 秒，慢机器上也留足余量
    pdf = _pdf(10)

    async def scenario():
        return await asyncio.gather(
            *(ocr_engine.ocr_page(pdf, n) for n in range(1, 11))
        )

    assert asyncio.run(scenario()) == ["识别文字"] * 10  # 总共约 1.5 秒，超过单页超时


def test_pdf_parsing_runs_off_event_loop(monkeypatch):
    # 上传时解析 PDF 在专用线程里跑，不占事件循环
    seen = []
    orig = file_parser._pdf_page_texts

    def spy(content):
        seen.append(threading.current_thread().name)
        return orig(content)

    monkeypatch.setattr(file_parser, "_pdf_page_texts", spy)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72), "虚构审批表正文，文字层足够长，判定为文字版。" * 3, fontname="china-s"
    )
    out = asyncio.run(file_parser._parse_pdf("审批表.pdf", doc.tobytes(), "审批表"))
    assert out["is_scanned"] is False and out["page_count"] == 1
    assert seen and seen[0].startswith("parse")


def test_slot_held_until_timed_out_request_finishes(monkeypatch):
    # 超时 / 取消只是不再等，线程里的请求还在跑：名额要等它真正结束才放出，
    # 否则排队的页马上开始，实际同时发往 DashScope 的请求超过上限（#92 评审）
    state = _fake_ocr(monkeypatch, 0.6)
    monkeypatch.setattr(ocr_engine, "OCR_GLOBAL_CONCURRENCY", 2)
    monkeypatch.setattr(ocr_engine, "OCR_PAGE_TIMEOUT", 0.2)
    pdf = _pdf(6)

    async def scenario():
        # 第一批：全部超时（请求要 0.6 秒，只等 0.2 秒）
        first = await asyncio.gather(
            *(ocr_engine.ocr_page(pdf, n) for n in range(1, 4)), return_exceptions=True
        )
        # 第二批：一页被取消，其余排队；旧请求线程还在跑时不得多发
        tasks = [asyncio.create_task(ocr_engine.ocr_page(pdf, n)) for n in range(4, 7)]
        await asyncio.sleep(0.05)
        tasks[0].cancel()
        second = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.8)  # 让被放弃的线程跑完
        return first, second

    first, second = asyncio.run(scenario())
    assert all(isinstance(e, TimeoutError) for e in first)
    assert isinstance(second[0], asyncio.CancelledError)
    assert state["max"] <= 2
