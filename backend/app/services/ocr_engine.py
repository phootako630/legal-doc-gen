# OCR 引擎封装：对扫描件 PDF 执行文字识别，返回按页结构化结果
#
# 当前实现：Qwen-VL-OCR API（阿里云 DashScope，OpenAI 兼容接口）
#   - 无需本地模型，无安装依赖，识别质量高
#   - 需要在 .env 中配置 DASHSCOPE_API_KEY
#
# ── BENCHMARK 备用：RapidOCR（本地 ONNX）────────────────────────────────────
# 若需对比测试，注释掉下方"当前实现"区块，取消注释"RapidOCR 实现"区块
# 依赖：pip install rapidocr-onnxruntime onnxruntime
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from app.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_API_KEY_BACKUP,
    DASHSCOPE_BASE_URL,
    OCR_GLOBAL_CONCURRENCY,
    OCR_MAX_CONCURRENCY,
    OCR_PAGE_TIMEOUT,
    OCR_TIMEOUT,
    QWEN_OCR_ENABLE_THINKING,
    QWEN_OCR_MODEL,
)
from app.services import run_log, upload_progress

logger = logging.getLogger(__name__)

# ── 按需 OCR 的并发控制 ──────────────────────────────────────────────────────
# DashScope 请求是同步的（requests），一页 5–15 秒。若放在默认线程池里，几个律师同时分析就会
# 占满默认池（2 核服务器只有 6 个线程），登录校验（asyncio.to_thread）等所有请求跟着排队，
# 整个网站像卡死一样。故 OCR 用专用线程池，并用全局信号量限制同时识别的页数：超出的在
# 信号量上排队（单页超时从拿到名额后才开始算，排队不会被当成超时）。
# 名额要等线程里的请求真正结束才释放：超时 / 取消只是不再等它，requests 还在跑，此时就放出名额，
# 实际同时发往 DashScope 的请求会超过上限（#92 评审）。线程数与名额相同即可
_OCR_POOL = ThreadPoolExecutor(
    max_workers=OCR_GLOBAL_CONCURRENCY, thread_name_prefix="ocr"
)
# 渲染页面图片是本地 CPU 活，2 核服务器给 2 个线程
_RENDER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr-render")
# 信号量绑定事件循环：服务里只有一个循环；测试里每次 asyncio.run 是新循环，各建一个
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _ocr_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = _slots[loop] = asyncio.Semaphore(OCR_GLOBAL_CONCURRENCY)
    return sem


class OcrPage(TypedDict):
    page_num: int  # 从 1 开始
    text: str  # 该页识别出的全部文字，行间用 \n 分隔


class OcrResult(TypedDict):
    pages: list[OcrPage]
    full_text: str  # 所有页文字拼接，页间用 \n\n 分隔


# ── 当前实现：Qwen-VL-OCR ────────────────────────────────────────────────────


async def ocr_pdf(pdf_bytes: bytes) -> OcrResult:
    """
    对 PDF 字节流执行 OCR，超时 OCR_TIMEOUT 秒后抛出异常。

    Raises:
        RuntimeError: API Key 未配置，或 OCR 处理失败
        TimeoutError: OCR 超时
    """
    if not _keys():
        raise RuntimeError(_MISSING_KEY)

    logger.info("整份 OCR：%d 字节", len(pdf_bytes))
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ocr_sync_qwen, pdf_bytes),
            timeout=OCR_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"OCR 处理超时（超过 {OCR_TIMEOUT} 秒），文件可能过大或图像质量过低"
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"OCR 处理失败：{exc}") from exc
    return result


_MISSING_KEY = "未配置 DASHSCOPE_API_KEY，请在 backend/.env 中填入阿里云 DashScope API Key"
# 主 key（免费额度）被拒时改用付费备用 key：401 / 403（额度用完、无权限）才切换，429 限流不切换
_SWITCH_STATUSES = {401, 403}
_use_backup = False  # 切换后本次进程一直用备用 key，不再先撞一次主 key


def _keys() -> list[str]:
    """按尝试顺序返回可用 key（主 key 在前；已切换到备用 key 时只剩备用 key）。"""
    primary = (DASHSCOPE_API_KEY or "").strip()
    backup = (DASHSCOPE_API_KEY_BACKUP or "").strip()
    keys = [backup] if _use_backup else [primary, backup]
    return [k for k in keys if k]


def _post_with_fallback(payload: dict):
    """先用主 key 调用；被拒（401/403）且配了备用 key 时用备用 key 重试一次，并记住切换。"""
    import requests

    global _use_backup
    keys = _keys()
    if not keys:
        raise RuntimeError(_MISSING_KEY)
    for i, key in enumerate(keys):
        resp = requests.post(
            DASHSCOPE_BASE_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=OCR_PAGE_TIMEOUT,
        )
        is_last = i == len(keys) - 1
        if resp.status_code in _SWITCH_STATUSES and not is_last:
            # 不打印 key，只记状态码，方便排查免费额度何时用完
            logger.warning("主 key 被拒（HTTP %s），改用付费备用 key", resp.status_code)
            # 从这里起开始产生费用：单独记一条事件（#75），汇总时一眼能看到
            run_log.log_event("dashscope_key_switch", reason=resp.status_code, path="ocr")
            _use_backup = True
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("OCR 处理失败：没有可用的 DashScope key")  # 不会走到


def _ocr_image_b64(img_b64: str) -> str:
    """对单页 PNG（base64）调用 Qwen-VL-OCR，返回识别文本（异常/异常结构返回空串）。"""
    payload = {
        "model": QWEN_OCR_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": f"data:image/png;base64,{img_b64}"},
                        {"text": "请识别图中所有文字内容，保持原有格式输出。"},
                    ],
                }
            ]
        },
        "parameters": {
            "result_format": "message",
            "enable_thinking": QWEN_OCR_ENABLE_THINKING,
        },
    }

    resp = _post_with_fallback(payload)
    data = resp.json()

    # DashScope 原生 API 响应结构：output.choices[0].message.content[0].text
    try:
        return data["output"]["choices"][0]["message"]["content"][0]["text"].strip()
    except (KeyError, IndexError):
        # 只记结构，不记内容：返回里可能带识别出的案件原文
        keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        logger.warning("OCR 返回结构异常，顶层字段：%s", keys)
        return ""


def _ocr_one_page(page_idx: int, img_b64: str, total_pages: int) -> OcrPage:
    """对单页图片调用 Qwen-VL-OCR，供整份并发 OCR 的线程池调度（带进度上报）。"""
    text = _ocr_image_b64(img_b64)
    logger.info("第 %d/%d 页识别完成，%d 字", page_idx + 1, total_pages, len(text))
    upload_progress.page_done()
    return {"page_num": page_idx + 1, "text": text}


def pdf_page_count(pdf_bytes: bytes) -> int:
    """PDF 总页数（打不开返回 0）。供按需 OCR 在调用方未带页数时兜底。"""
    import fitz  # PyMuPDF

    try:
        return fitz.open(stream=pdf_bytes, filetype="pdf").page_count
    except Exception:
        return 0


def _render_page_b64(pdf_bytes: bytes, page_num: int) -> str | None:
    """把 PDF 第 page_num 页（1 起）渲染为 PNG base64；页码越界返回 None。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_num < 1 or page_num > doc.page_count:
        return None
    pix = doc[page_num - 1].get_pixmap(dpi=150)
    return base64.b64encode(pix.tobytes("png")).decode()


async def ocr_page(pdf_bytes: bytes, page_num: int) -> str:
    """
    对扫描件 PDF 的**指定页**（1 起）做 OCR，返回该页文本（按需 OCR，非整份）。

    Raises:
        RuntimeError: API Key 未配置，或 OCR 处理失败
        TimeoutError: 单页 OCR 超时
    """
    if not _keys():
        raise RuntimeError(_MISSING_KEY)

    loop = asyncio.get_running_loop()
    slot = _ocr_slot()
    await slot.acquire()
    # 当前在线程里跑的任务：等待时套 shield，超时 / 取消只放弃等待，任务本身要到线程结束才算完成
    running: asyncio.Future | None = None
    try:
        # 名额里渲染 + 识别：同时渲染出来、占着内存的页图也不超过名额数
        running = loop.run_in_executor(
            _RENDER_POOL, _render_page_b64, pdf_bytes, page_num
        )
        img_b64 = await asyncio.shield(running)
        if img_b64 is None:
            return ""
        # 带上调用方的上下文（run_id / 节点名）：线程里切换 key 时记的事件才能归到本案件
        running = loop.run_in_executor(
            _OCR_POOL, contextvars.copy_context().run, _ocr_image_b64, img_b64
        )
        return await asyncio.wait_for(asyncio.shield(running), timeout=OCR_PAGE_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"第 {page_num} 页 OCR 超时（超过 {OCR_PAGE_TIMEOUT} 秒）"
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"第 {page_num} 页 OCR 处理失败：{exc}") from exc
    finally:
        if running is None or running.done():
            slot.release()
        else:
            # 线程还在跑（超时或请求被取消）：等它真正结束再放出名额
            running.add_done_callback(lambda f: _release_after(f, slot))


def _release_after(fut: asyncio.Future, slot: asyncio.Semaphore) -> None:
    """放弃等待的线程任务结束后放出名额；顺手取走它的异常，免得事件循环报「异常没人取」。"""
    if not fut.cancelled():
        fut.exception()
    slot.release()


def _ocr_sync_qwen(pdf_bytes: bytes) -> OcrResult:
    """
    并发调用 Qwen-VL-OCR 对每页做识别，在线程池中运行以避免阻塞事件循环。

    页数一多，逐页串行调用很容易撑爆整份文件的总超时（例如 45 页的合同，
    每页 2-3 秒也要 100+ 秒）——改为多页并发请求，用 OCR_MAX_CONCURRENCY
    控制并发数，避免打爆 DashScope 的限流。
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = doc.page_count
    upload_progress.start_ocr(total_pages)

    # 先在主线程把所有页渲染为 base64 图片（本地操作，不受网络并发影响）
    page_images: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        page_images.append(base64.b64encode(pix.tobytes("png")).decode())

    with ThreadPoolExecutor(max_workers=OCR_MAX_CONCURRENCY) as executor:
        futures = [
            executor.submit(_ocr_one_page, idx, img_b64, total_pages)
            for idx, img_b64 in enumerate(page_images)
        ]
        # 按提交顺序取结果即按页码顺序——某一页失败会在这里抛出，
        # with 块退出前仍会等待其余已提交的页面完成（无法强行取消已发出的请求）
        pages: list[OcrPage] = [f.result() for f in futures]

    full_text = "\n\n".join(p["text"] for p in pages if p["text"])
    return {"pages": pages, "full_text": full_text}


# ── BENCHMARK 备用：RapidOCR（本地 ONNX）────────────────────────────────────
# 取消注释以下代码并注释上方"当前实现"区块，即可切换回本地 OCR

# # RapidOCR 实例（懒加载，避免启动时初始化耗时）
# _ocr_instance = None
#
#
# def _get_ocr():
#     """懒加载 RapidOCR 单例，首次调用时才初始化模型。"""
#     global _ocr_instance
#     if _ocr_instance is None:
#         print("[OCR ENGINE] 初始化 RapidOCR ...", flush=True)
#         from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]
#         _ocr_instance = RapidOCR()
#         print("[OCR ENGINE] RapidOCR 初始化完成", flush=True)
#     return _ocr_instance
#
#
# async def ocr_pdf(pdf_bytes: bytes) -> OcrResult:
#     print(f"[OCR ENGINE] ocr_pdf() called, pdf_bytes len={len(pdf_bytes)}", flush=True)
#     loop = asyncio.get_event_loop()
#     try:
#         result = await asyncio.wait_for(
#             loop.run_in_executor(None, _ocr_sync_rapidocr, pdf_bytes),
#             timeout=OCR_TIMEOUT,
#         )
#     except asyncio.TimeoutError as exc:
#         raise TimeoutError(
#             f"OCR 处理超时（超过 {OCR_TIMEOUT} 秒），文件可能过大或图像质量过低"
#         ) from exc
#     except Exception as exc:
#         raise RuntimeError(f"OCR 处理失败：{exc}") from exc
#     return result
#
#
# def _ocr_sync_rapidocr(pdf_bytes: bytes) -> OcrResult:
#     """同步执行 RapidOCR，在线程池中运行以避免阻塞事件循环。"""
#     import fitz  # PyMuPDF
#     import numpy as np
#     from PIL import Image
#
#     ocr = _get_ocr()
#     doc = fitz.open(stream=pdf_bytes, filetype="pdf")
#     pages: list[OcrPage] = []
#
#     for page_idx, page in enumerate(doc):
#         pix = page.get_pixmap(dpi=150)
#         img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
#         img_np = np.array(img)
#
#         result, _ = ocr(img_np)
#
#         lines: list[str] = []
#         if result:
#             for item in result:
#                 if len(item) >= 2:
#                     text_str = str(item[1]).strip()
#                     if text_str:
#                         lines.append(text_str)
#
#         pages.append({"page_num": page_idx + 1, "text": "\n".join(lines)})
#
#     full_text = "\n\n".join(p["text"] for p in pages if p["text"])
#     return {"pages": pages, "full_text": full_text}
