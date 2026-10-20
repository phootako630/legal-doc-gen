# 文件解析调度：根据文件类型选择 PyMuPDF / python-docx / PaddleOCR
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import UploadFile

from app.config import SCANNED_PDF_TEXT_THRESHOLD
from app.services import file_store, ocr_engine

# 文件类型取值（上传页律师可改；分析接口按此校验）
DocType = Literal["审批表", "合同", "补充协议", "验收报告", "战略合同", "未知"]

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
    # 律师反馈（徐州苏宁）：主合同写「争议解决……按战略合同执行」，条款在另一份战略 / 框架合同里。
    # 单独成一类：不当主合同取当事人、签约日期、台数，只在主合同引用时去里面找条款
    "战略合同": "战略合同",
    "战略合作": "战略合同",
    "框架合同": "战略合同",
    "框架协议": "战略合同",
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
    ("检验报告", "验收报告"),
    ("验收报告", "验收报告"),
    ("合同", "合同"),
]
_CONTENT_HEAD_CHARS = 300  # 只看开头：正文后文提到「验收报告」「合同」不代表文件类型


def _title_lines(text: str) -> str:
    """
    标题区：开头最多 3 个非空短行，遇到「甲方：」这类当事人行或「第一条」就停。补充协议只按标题
    判断——主合同正文写「未尽事宜，双方另行签订补充协议」不能把整份合同当成补充协议（#53 复审）。
    """
    title = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if len(title) >= 3 or len(line) > 40 or re.search(r"[:：]|^第[一二三四五六七八九十\d]+[条章]", line):
            break
        title.append(line)
    return "".join(title)


def _identify_type(filename: str, text: str = "") -> DocType:
    for keyword, doc_type in _TYPE_KEYWORDS.items():
        if keyword in filename:
            return doc_type
    title = _title_lines(text)
    if "补充协议" in title:
        return "补充协议"
    if re.search(r"战略合同|战略合作|框架合同|框架协议", title):
        return "战略合同"
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
        if identified_type == "未知":
            identified_type = await _identify_scanned_by_title(filename, content)
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


async def _identify_scanned_by_title(filename: str, content: bytes) -> DocType:
    """
    扫描件文件名认不出类型（如「001.pdf」）时，只 OCR 第 1 页、按标题再认一次：每份这样的扫描件
    多花一页识别费用，换来材料确认页少让律师手选。OCR 不可用或失败时仍为「未知」，交给律师在
    材料确认页选择。识别出的文字不保留：按需读取时仍从第 1 页读（扫描件 text 为空是后续节点判断
    「还没读过」的依据）。
    """
    try:
        first_page = await ocr_engine.ocr_page(content, 1)
    except (RuntimeError, TimeoutError) as exc:
        print(f"[UPLOAD] 扫描件首页识别失败，类型留给律师选择：{filename} — {exc}", flush=True)
        return "未知"
    return _identify_type(filename, first_page or "")


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
