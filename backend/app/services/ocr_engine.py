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
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

from app.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    OCR_MAX_CONCURRENCY,
    OCR_PAGE_TIMEOUT,
    OCR_TIMEOUT,
    QWEN_OCR_MODEL,
)
from app.services import upload_progress

logger = logging.getLogger(__name__)
print(f"[OCR ENGINE] loading: {__file__}  (version: Qwen-VL-OCR)", flush=True)


class OcrPage(TypedDict):
    page_num: int  # 从 1 开始
    text: str      # 该页识别出的全部文字，行间用 \n 分隔


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
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 backend/.env 中填入阿里云 DashScope API Key")

    print(f"[OCR ENGINE] ocr_pdf() called, pdf_bytes len={len(pdf_bytes)}", flush=True)
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


def _ocr_one_page(page_idx: int, img_b64: str, total_pages: int) -> OcrPage:
    """对单页图片调用 Qwen-VL-OCR，供线程池并发调度。"""
    import requests

    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
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
        },
    }

    resp = requests.post(DASHSCOPE_BASE_URL, headers=headers, json=payload, timeout=OCR_PAGE_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # DashScope 原生 API 响应结构：output.choices[0].message.content[0].text
    try:
        text = data["output"]["choices"][0]["message"]["content"][0]["text"]
    except (KeyError, IndexError):
        print(f"[OCR ENGINE] page {page_idx + 1} unexpected response: {data}", flush=True)
        text = ""

    print(f"[OCR ENGINE] page {page_idx + 1}/{total_pages} done, chars={len(text)}", flush=True)
    upload_progress.page_done()
    return {"page_num": page_idx + 1, "text": text.strip()}


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
