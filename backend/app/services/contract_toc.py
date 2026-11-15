# 长扫描合同按目录定位读页（#12；律师新案件：230 页安装合同，争议条款在第 85 页「专用条件」、
# 仲裁机构在第 91 页附录，从第 1 页顺序读到 50 页上限还在技术参数表里，两处都读不到）。
#
# 做法：按需 OCR 读完第一批页后，若其中有目录页，用代码解析目录各行（标题 + 页数 / 页码），
# 估出各部分在 PDF 里的页码范围，把与目标条款有关的部分（标题写着争议 / 付款等，或「合同书 / 专用条件 /
# 通用条件」这类条款部分）排到前面先读；其余页仍按原顺序在后面。停止条件不变（找到条款正文即停）。
#
# 目录有两种写法：
# - 页数：「| 1 | 本合同书 | 4 |」，表头写「页数」，数字是该部分有几页；各部分常另有一页分隔页（不计入
#   页数），估算会逐段漂移，所以范围两侧各放宽 _MARGIN 页；
# - 页码：「第十九条 争议的解决 …… 85」，数字是起始页；印刷页码与 PDF 页码之间差封面、目录等几页，
#   范围终点再放宽目录所在页数。
# 解析不出（没有目录、行数太少、页码倒退）时返回空，照原顺序读。纯函数、可单测。
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.doc_search import is_toc_page

_MARGIN = 3  # 按页数估算时，各部分范围两侧放宽的页数
_MIN_ROWS = 2  # 目录至少解析出这么多行才用

# 与目标条款对应的目录标题关键词
_TARGET_TITLE_RE: dict[str, re.Pattern[str]] = {
    "dispute": re.compile(r"争议|争端|纠纷|仲裁|诉讼|管辖"),
    "payment": re.compile(r"付款|支付|价款|结算|合同价"),
    "breach": re.compile(r"违约"),
    "warranty": re.compile(r"质保|保修"),
}
# 写合同条款的部分（目录只列文件名、不列条款标题时，先读这些部分）
_CLAUSE_SECTION_RE = re.compile(
    r"合同书|协议书|合同条款|合同条件|专用条件|通用条件|专用条款|通用条款|特殊条款|补充条款|补充协议"
)
# 目录行：表格「| 序号 | 标题 | 数字 |」或「标题 …… 数字」。数字前紧跟「/」的（「SC/14」「2 of 2」这类分册页码）不算
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_LINE_ROW_RE = re.compile(r"^\s*(?P<title>.*?[一-鿿].*?)[\s.．…·_—\-]*(?<![/／\d])(?P<num>\d{1,3})\s*$")
_NUM_CELL_RE = re.compile(r"^\d{1,3}$")
_SEQ_CELL_RE = re.compile(r"^(?:第?[\d一二三四五六七八九十]{1,3}[章节条部分]?|[\d.．]+)$")


@dataclass(frozen=True)
class TocRow:
    title: str
    num: int


def _parse_rows(text: str) -> list[TocRow]:
    rows: list[TocRow] = []
    for line in text.splitlines():
        m = _TABLE_ROW_RE.match(line)
        if m:
            cells = [c.strip() for c in m.group(1).split("|")]
            if not cells or not _NUM_CELL_RE.match(cells[-1]):
                continue  # 表头、分隔行
            titles = [c for c in cells[:-1] if c and not _SEQ_CELL_RE.match(c) and re.search(r"[一-鿿]", c)]
            if titles:
                rows.append(TocRow(max(titles, key=len), int(cells[-1])))
            continue
        m = _LINE_ROW_RE.match(line)
        if m and "目录" not in m.group("title"):
            rows.append(TocRow(m.group("title").strip(), int(m.group("num"))))
    return rows


def _ranges(rows: list[TocRow], counts: bool, toc_page: int, page_total: int) -> list[tuple[TocRow, int, int]]:
    """各目录行估算的 PDF 页码范围（含两端，已放宽）。"""
    out: list[tuple[TocRow, int, int]] = []
    if counts:
        cursor = toc_page + 1
        for row in rows:
            start, end = cursor, cursor + row.num  # 分隔页 + 该部分 row.num 页
            out.append((row, start - _MARGIN, end + _MARGIN))
            cursor = end + 1
    else:
        for i, row in enumerate(rows):
            nxt = rows[i + 1].num if i + 1 < len(rows) else page_total
            out.append((row, row.num, max(nxt - 1, row.num) + toc_page))
    return [(r, max(1, s), min(page_total, e)) for r, s, e in out if s <= page_total]


def toc_priority_pages(pages: list[dict], page_total: int, targets: set[str]) -> list[int]:
    """
    按已读页里的目录，估出与目标条款有关的部分在 PDF 里的页码，按先读顺序返回；没有可用目录返回空。
    标题直接写着目标条款（争议 / 付款……）的部分排最前，其次是合同书 / 专用条件等条款部分，各按文档顺序。
    """
    toc = sorted((p for p in pages if is_toc_page(p.get("text") or "")), key=lambda p: p["page"])
    if not toc:
        return []
    # 只用第一处目录（连同紧接着的续页）：后面的目录多半是某一部分自己的分目录，页码另起
    first = [toc[0]]
    for p in toc[1:]:
        if p["page"] == first[-1]["page"] + 1:
            first.append(p)
    text = "\n".join(p.get("text") or "" for p in first)
    rows = _parse_rows(text)
    if len(rows) < _MIN_ROWS:
        return []
    compact = re.sub(r"\s+", "", text)
    counts = "页数" in compact and "页码" not in compact
    if not counts and any(b.num < a.num for a, b in zip(rows, rows[1:])):
        return []  # 页码倒退：多半没解析对
    ranges = _ranges(rows, counts, first[-1]["page"], page_total)
    patterns = [_TARGET_TITLE_RE[t] for t in sorted(targets) if t in _TARGET_TITLE_RE]
    direct = [rg for rg in ranges if any(p.search(rg[0].title) for p in patterns)]
    clause = [rg for rg in ranges if rg not in direct and _CLAUSE_SECTION_RE.search(rg[0].title)]
    order: list[int] = []
    for _, start, end in direct + clause:
        order.extend(n for n in range(start, end + 1) if n not in order)
    return order
