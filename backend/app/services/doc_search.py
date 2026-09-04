# 文档内关键词检索：在（已 OCR 的）逐页文本里定位条款所在页与片段。
#
# 设计意图（对应 CLAUDE.md「search_in_docs」）：
#   扫描合同逐页 OCR 后，用关键词定位付款/违约/争议三类条款出现在第几页，
#   供 agent 判断“是否已拿到该找的条款、可以停止继续 OCR”。纯函数、可单测。
from __future__ import annotations

from dataclasses import dataclass

# 三类条款的定位关键词（命中任一即认为该条款出现在此页）
CLAUSE_KEYWORDS: dict[str, list[str]] = {
    "payment": ["付款", "支付", "价款", "货款", "结算"],
    "breach": ["违约", "逾期", "利率", "滞纳", "赔偿"],
    "dispute": ["争议", "诉讼", "仲裁", "管辖", "纠纷"],
}

_SNIPPET_RADIUS = 30  # 命中片段前后各取的字符数


@dataclass(frozen=True)
class SearchHit:
    page: int | None
    keyword: str
    snippet: str


def search_in_docs(pages: list[dict], keywords: list[str]) -> list[SearchHit]:
    """
    在逐页文本里查找任一 keyword，返回命中列表（每页每关键词记首个命中）。

    pages: [{"page": int|None, "text": str}, ...]
    """
    hits: list[SearchHit] = []
    for pg in pages:
        text = pg.get("text") or ""
        page_no = pg.get("page")
        for kw in keywords:
            idx = text.find(kw)
            if idx == -1:
                continue
            start = max(0, idx - _SNIPPET_RADIUS)
            end = min(len(text), idx + len(kw) + _SNIPPET_RADIUS)
            snippet = text[start:end].replace("\n", " ").strip()
            hits.append(SearchHit(page=page_no, keyword=kw, snippet=snippet))
    return hits


def locate_clauses(
    pages: list[dict],
    clause_keywords: dict[str, list[str]] | None = None,
) -> set[str]:
    """返回在 pages 中已能定位到的条款名集合（payment / breach / dispute）。"""
    clause_keywords = clause_keywords or CLAUSE_KEYWORDS
    found: set[str] = set()
    for clause, kws in clause_keywords.items():
        if search_in_docs(pages, kws):
            found.add(clause)
    return found
