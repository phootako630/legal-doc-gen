# 扫描件原件页渲染（#111）：审核页「查看原件第 N 页」用，把暂存的 PDF 某一页渲染成 PNG。
#
# 渲染是本地 CPU 活，放专用线程池（与 OCR 的约定一致）：2 核服务器的默认线程池只有 6 个线程，
# 律师连续翻看原件时占满默认池，登录校验等所有请求都会跟着卡住。池只给 1 个线程——
# 翻看原件不急，不能和按需 OCR 的渲染（ocr_engine._RENDER_POOL，2 个线程）抢 CPU。
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF

# 与 OCR 送识别的图片同一分辨率：律师看到的就是 OCR 看到的
PAGE_IMAGE_DPI = 150

_VIEW_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="page-view")


def _render(pdf_bytes: bytes, page_num: int) -> bytes | None:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_num < 1 or page_num > doc.page_count:
            return None
        return doc[page_num - 1].get_pixmap(dpi=PAGE_IMAGE_DPI).tobytes("png")
    finally:
        doc.close()


async def render_page_png(pdf_bytes: bytes, page_num: int) -> bytes | None:
    """PDF 第 page_num 页（1 起）的 PNG；页码越界返回 None。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_VIEW_POOL, _render, pdf_bytes, page_num)
