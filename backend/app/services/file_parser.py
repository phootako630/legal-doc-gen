# 文件解析调度：根据文件类型选择 PyMuPDF / python-docx / PaddleOCR
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import UploadFile

from app.config import SCANNED_PDF_TEXT_THRESHOLD
from app.services.ocr_engine import ocr_pdf

# 文件名关键词 → 文件类型（简单规则，后续可升级为 LLM 判断）
_TYPE_KEYWORDS: dict[str, str] = {
    "审批": "审批表",
    "合同": "合同",
    "验收": "验收报告",
}


def _identify_type(filename: str) -> str:
    for keyword, doc_type in _TYPE_KEYWORDS.items():
        if keyword in filename:
            return doc_type
    return "未知"


async def parse_file(upload_file: UploadFile) -> dict[str, Any]:
    """解析单个上传文件，返回符合 ParsedFile 结构的字典。"""
    filename = upload_file.filename or "未知文件"
    content = await upload_file.read()
    identified_type = _identify_type(filename)

    if filename.lower().endswith(".pdf"):
        return await _parse_pdf(filename, content, identified_type)
    elif filename.lower().endswith((".docx", ".doc")):
        return _parse_docx(filename, content, identified_type)
    else:
        raise ValueError(f"不支持的文件格式：{filename}，仅支持 .pdf / .docx")


async def _parse_pdf(filename: str, content: bytes, identified_type: str) -> dict[str, Any]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    page_count = doc.page_count
    text = "".join(page.get_text() for page in doc)

    is_scanned = len(text.strip()) < SCANNED_PDF_TEXT_THRESHOLD
    if is_scanned:
        # 扫描件走 OCR，取结构化结果的全文字段
        ocr_result = await ocr_pdf(content)
        text = ocr_result["full_text"]

    if not text.strip():
        raise ValueError(f"文件《{filename}》内容为空，无法提取有效文本（可能为空白页或图像质量过低）")

    return {
        "filename": filename,
        "identified_type": identified_type,
        "text": text,
        "is_scanned": is_scanned,
        "page_count": page_count,
    }


def _parse_docx(filename: str, content: bytes, identified_type: str) -> dict[str, Any]:
    import io
    from docx import Document

    doc = Document(io.BytesIO(content))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if not text.strip():
        raise ValueError(f"文件《{filename}》内容为空，无法提取有效文本")

    return {
        "filename": filename,
        "identified_type": identified_type,
        "text": text,
        "is_scanned": False,
        "page_count": 1,
    }
