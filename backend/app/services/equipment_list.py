# 合同设备清单：数出型号含「VGE」的电梯台数（家用电梯，无需取得验收报告）。
#
# 律师补充确认单第 2 题：合同台数与验收报告台数不一致时，先看合同里有没有 VGE 型号；
# 验收报告台数 = 合同台数 − VGE 台数 就按验收报告台数继续，并提醒存在家用电梯。
# OCR 出来的设备清单是 Markdown 或 HTML 表格：按表头找「型号」「数量 / 台数」两列再逐行累加。
# 表格结构认不出来（如横排表）时只报告「出现过 VGE」，台数交给律师确认，不猜。
from __future__ import annotations

import re

_VGE_RE = re.compile(r"VGE", re.IGNORECASE)
_HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_INT_RE = re.compile(r"\d+")


def _table_rows(text: str) -> list[list[str]]:
    """把文本里的 Markdown 表格行与 HTML 表格行统一拆成单元格列表（按出现顺序）。"""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # Markdown 分隔行
            rows.append(cells)
    for tr in _HTML_ROW_RE.findall(text):
        rows.append([_TAG_RE.sub("", c).strip() for c in _HTML_CELL_RE.findall(tr)])
    return rows


def count_vge_units(text: str) -> tuple[int, bool]:
    """返回 (设备清单中 VGE 型号的台数合计, 文本中是否出现过 VGE)。"""
    found = bool(_VGE_RE.search(text or ""))
    if not found:
        return 0, False
    total = 0
    model_idx = qty_idx = None
    for cells in _table_rows(text):
        header_model = next((i for i, c in enumerate(cells) if "型号" in c), None)
        header_qty = next(
            (i for i, c in enumerate(cells) if "数量" in c or "台数" in c), None
        )
        if header_model is not None and header_qty is not None:
            model_idx, qty_idx = header_model, header_qty
            continue
        if (
            model_idx is None
            or qty_idx is None
            or max(model_idx, qty_idx) >= len(cells)
        ):
            continue
        if _VGE_RE.search(cells[model_idx]):
            m = _INT_RE.search(cells[qty_idx])
            if m:
                total += int(m.group())
    return total, True
