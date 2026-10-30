# 合同设备清单：数出型号含「VGE」的电梯台数（家用电梯，无需取得验收报告）。
#
# 律师补充确认单第 2 题：合同台数与验收报告台数不一致时，先看合同里有没有 VGE 型号；
# 验收报告台数 = 合同台数 − VGE 台数 就按验收报告台数继续，并提醒存在家用电梯。
# OCR 出来的设备清单是 Markdown 或 HTML 表格：按表头找「型号」「数量 / 台数」两列再逐行累加。
# 文字版 PDF 的清单常是空格 / 制表符对齐的纯文字（#112）：按表头行切列，只取「型号」「台数」两列；
# 不按行匹配「VGE……数字」，那样会把层站「18/18/18」之类的数字当成台数。
# 表格结构认不出来（如横排表、某行切出的列数与表头对不上）时只报告「出现过 VGE」，台数交给律师确认，不猜。
from __future__ import annotations

import re

_VGE_RE = re.compile(r"VGE", re.IGNORECASE)
_HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_INT_RE = re.compile(r"\d+")


def table_rows(text: str) -> list[list[str]]:
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


# 纯文字表格的列分隔：制表符，或两个及以上空白（型号里的单个空格不算分隔）
_PLAIN_SPLIT_RE = re.compile(r"\t+|[ \u3000]{2,}")
_TOTAL_ROW_RE = re.compile(r"合\s*计|总\s*计|小\s*计")
_PROSE_RE = re.compile(r"[，。：；:;]")  # 正文句读；清单行里一般没有


def _header_cols(cells: list[str]) -> tuple[int, int] | None:
    model = next((i for i, c in enumerate(cells) if "型号" in c), None)
    qty = next((i for i, c in enumerate(cells) if "数量" in c or "台数" in c), None)
    return (model, qty) if model is not None and qty is not None else None


def _count_plain_text(text: str) -> int | None:
    """
    空格 / 制表符对齐的文字版清单（#112）。返回 VGE 台数合计；没有这种清单返回 0；
    清单里有 VGE 行却对不上表头的列（切出的列数不同、台数不是数字）返回 None，交给律师。
    表头之后遇到切不出两列以上的行（正文、空行）就算表格结束。
    """
    total = 0
    cols: tuple[int, int] | None = None
    width = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") or "<t" in stripped:
            cols = None  # Markdown / HTML 表格由 table_rows 处理
            continue
        cells = [c.strip() for c in _PLAIN_SPLIT_RE.split(stripped) if c.strip()]
        header = _header_cols(cells) if len(cells) >= 2 else None
        if header:
            cols, width = header, len(cells)
            continue
        if cols is None:
            continue
        if len(cells) < 2:
            # 切不出两列：是正文（带句读）就算表格结束；含 VGE、又不像正文的，是没对齐的清单行，
            # 已数到的只是一部分，不能当完整结果（#137 评审）
            if _VGE_RE.search(stripped) and not _PROSE_RE.search(stripped):
                return None
            cols = None
            continue
        if _TOTAL_ROW_RE.search(stripped) or not _VGE_RE.search(stripped):
            continue
        model_idx, qty_idx = cols
        qty = cells[qty_idx] if len(cells) == width else ""
        if not _VGE_RE.search(cells[model_idx] if len(cells) == width else "") or not re.fullmatch(r"\d+\s*台?", qty):
            return None  # 这一行对不上列：不猜
        total += int(_INT_RE.search(qty).group())
    return total


def count_vge_units(text: str) -> tuple[int, bool]:
    """返回 (设备清单中 VGE 型号的台数合计, 文本中是否出现过 VGE)；数不准时台数为 0，交给律师。"""
    found = bool(_VGE_RE.search(text or ""))
    if not found:
        return 0, False
    plain = _count_plain_text(text)
    if plain is None:
        return 0, True
    total = plain
    model_idx = qty_idx = None
    for cells in table_rows(text):
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
