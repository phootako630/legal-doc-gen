# 文件解析调度：根据文件类型选择 PyMuPDF / python-docx / PaddleOCR
from __future__ import annotations

import re
from typing import Any

from fastapi import UploadFile

from app.config import SCANNED_PDF_TEXT_THRESHOLD
from app.services import file_store

# 文件名关键词 → 文件类型（简单规则，后续可升级为 LLM 判断）
# 顺序有意义（按插入顺序匹配）：《XX合同补充协议》《合同修改单》也含「合同」，须先判补充协议
_TYPE_KEYWORDS: dict[str, str] = {
    # 确认单（五）第 8 题：补充协议较常见，常见文件名《XXX补充协议》《合同修改单》《设备补协》。
    # 单独成一类：不当作合同去按需 OCR，只提醒律师并写「双方签订补充协议……」一句
    "补充协议": "补充协议",
    "补协": "补充协议",
    "修改单": "补充协议",
    "变更协议": "补充协议",
    "审批": "审批表",
    # 律师反馈：审批表常以「诉讼流程」命名，扫描件读不到正文时只能靠文件名
    "诉讼流程": "审批表",
    "合同": "合同",
    "验收": "验收报告",
    # 实务中验收材料多为《电梯监督检验报告》，与验收报告归为同一类（见 CLAUDE.md）
    "检验": "验收报告",
}


# 文件名认不出时看正文开头（标题所在处）：实务中审批表常以流程名命名
# （「AH2204383某某公司安装诉讼流程.pdf」），正文标题才是「KA大客户收款不良合同诉讼审批表」。
# 顺序有意义：审批表正文里也有「合同」二字，须先判审批表。
_CONTENT_KEYWORDS: list[tuple[str, str]] = [
    ("审批表", "审批表"),
    ("补充协议", "补充协议"),
    ("检验报告", "验收报告"),
    ("验收报告", "验收报告"),
    ("合同", "合同"),
]
_CONTENT_HEAD_CHARS = 300  # 只看开头：正文后文提到「验收报告」「合同」不代表文件类型


def _identify_type(filename: str, text: str = "") -> str:
    for keyword, doc_type in _TYPE_KEYWORDS.items():
        if keyword in filename:
            return doc_type
    head = re.sub(r"\s+", "", text)[:_CONTENT_HEAD_CHARS]
    for keyword, doc_type in _CONTENT_KEYWORDS:
        if keyword in head:
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


async def _parse_pdf(
    filename: str, content: bytes, identified_type: str
) -> dict[str, Any]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    page_count = doc.page_count
    # 逐页文本单独保留（pages），用于抽取值回原文定位到具体页码（出处可追溯）；
    # text 仍按原方式拼接，保持与既有抽取链路的输入字节一致。
    per_page = [page.get_text() for page in doc]
    text = "".join(per_page)
    pages = [{"page": i + 1, "text": t} for i, t in enumerate(per_page)]

    is_scanned = len(text.strip()) < SCANNED_PDF_TEXT_THRESHOLD
    if is_scanned:
        # 扫描件不再在上传时整份 OCR（成本高、且多数条款字段可由审批表/验收报告补全）。
        # 只暂存原始字节并返回 file_id，交给 agent 在缺条款字段时按需逐页 OCR。
        file_id = file_store.put(content)
        return {
            "filename": filename,
            "identified_type": identified_type,
            "text": "",  # 暂无文本；build_combined_text 会以占位文案呈现
            "is_scanned": True,
            "page_count": page_count,
            "pages": [],  # 按需 OCR 后由 agent 回填
            "file_id": file_id,
        }

    if not text.strip():
        raise ValueError(
            f"文件《{filename}》内容为空，无法提取有效文本（可能为空白页或图像质量过低）"
        )
    if identified_type == "未知":
        identified_type = _identify_type(filename, text)

    return {
        "filename": filename,
        "identified_type": identified_type,
        "text": text,
        "is_scanned": False,
        "page_count": page_count,
        "pages": pages,
        "file_id": None,
    }


def _parse_docx(filename: str, content: bytes, identified_type: str) -> dict[str, Any]:
    import io
    from docx import Document

    doc = Document(io.BytesIO(content))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if not text.strip():
        raise ValueError(f"文件《{filename}》内容为空，无法提取有效文本")
    if identified_type == "未知":
        identified_type = _identify_type(filename, text)

    return {
        "filename": filename,
        "identified_type": identified_type,
        "text": text,
        "is_scanned": False,
        "page_count": 1,
        # Word 无固定分页概念，整体作第 1 页
        "pages": [{"page": 1, "text": text}],
        "file_id": None,
    }
