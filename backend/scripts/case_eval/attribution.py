# eval 错误归因（#108）：错了的扫描件字段，是 OCR 把字认错了，还是 OCR 文本里有正确值、抽取 / 规则取错了。
#
# 扫描件的「原文」就是 OCR 文本：OCR 认错时 LLM 照着错字抽，回原文锚定照样精确命中，锚定发现不了。
# 这里用确定性代码在 OCR 文本里找标准值（与应用锚定同一个 anchor_value：金额按数值、日期按日期、
# 文本去空白），只用已录制的 OCR 结果，不发任何请求：
#   - 抽取错误：系统所引那一页（没引页码则任一已识别页）的 OCR 文本里有标准值；
#   - 其他页有标准值：引用页没有、别的已识别页有——金额、日期在合同里常出现多次，不能直接算抽取错误；
#   - 疑似 OCR 错误：扫描件的页全部识别过，哪一页都找不到标准值；
#   - 可能未读到：找不到，但扫描件还有页没识别（按需 OCR 没读到那一页），不算 OCR 认错；
#   - 非 OCR 字段：值出自文字版材料（文字层里有标准值，或系统引的是文字版文件）；
#   - 规则推导：标准值是规则拼出的措辞（管辖句、违约金 / 利息措辞等），原文里本就没有，不归因。
from __future__ import annotations

import re
from typing import Any

from app.services.anchoring import anchor_value
from app.services.derived_fields import _DERIVERS

EXTRACT = "抽取错误"
OTHER_PAGE = "其他页有标准值"
OCR_SUSPECT = "疑似 OCR 错误"
UNREAD = "可能未读到"
NOT_OCR = "非 OCR 字段"
RULE = "规则推导"
CAUSES = (EXTRACT, OTHER_PAGE, OCR_SUSPECT, UNREAD, NOT_OCR, RULE)

# 派生字段里照抄原文的几项（名称、合同台数 / 总价）仍可能是 OCR 认错，照常归因；其余派生字段是规则措辞
_LITERAL_DERIVED = {"plaintiff_name_final", "defendant_name", "elevator_qty_contract", "contract_total_stated"}
_RULE_KEYS = {k for k, _, _ in _DERIVERS} - _LITERAL_DERIVED

_SRC_FILE_RE = re.compile(r"《([^》]+)》")
_OCR_MARK_RE = re.compile(r"OCR|扫描", re.IGNORECASE)


def _pages(f: dict) -> list[dict]:
    """文件里有文字的页（扫描件即已 OCR 的页）；没有逐页信息时整份当一页。"""
    pages = [p for p in f.get("pages") or [] if (p.get("text") or "").strip()]
    if pages:
        return pages
    return [{"page": None, "text": f["text"]}] if (f.get("text") or "").strip() else []


def _unread(f: dict) -> bool:
    count = f.get("page_count") or 0
    return bool(count) and len([p for p in f.get("pages") or [] if (p.get("text") or "").strip()]) < count


def _hit(expected: Any, text: str) -> bool:
    return anchor_value(expected, text).hit


def attribute(
    key: str,
    expected: Any,
    node: Any,
    files: list[dict],
    expected_source: str | None = None,
) -> str:
    """一个「错误 / 缺失」字段的归因（见文件头）。"""
    if key in _RULE_KEYS:
        return RULE  # 规则措辞：原文里碰巧出现同样的字（如「100%」）也不说明什么
    node = node if isinstance(node, dict) else {}
    src = str(node.get("src") or "")
    m = _SRC_FILE_RE.search(src)
    cited = next((f for f in files if m and f.get("filename") == m.group(1)), None)
    if cited is not None and not cited.get("is_scanned") and not _OCR_MARK_RE.search(src):
        return NOT_OCR

    if cited is not None:
        candidates = [cited]
    elif expected_source:
        candidates = [f for f in files if expected_source in (f.get("filename") or "")] or files
    else:
        candidates = files
    if any(not f.get("is_scanned") and any(_hit(expected, p["text"]) for p in _pages(f)) for f in candidates):
        return NOT_OCR
    scanned = [f for f in candidates if f.get("is_scanned")]
    if not scanned:
        return NOT_OCR

    page = node.get("page") if cited is not None and cited.get("is_scanned") else None
    found_pages = [p.get("page") for f in scanned for p in _pages(f) if _hit(expected, p["text"])]
    if found_pages:
        return EXTRACT if page is None or page in found_pages else OTHER_PAGE
    return UNREAD if any(_unread(f) for f in scanned) else OCR_SUSPECT
