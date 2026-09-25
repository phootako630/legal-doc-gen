# 文档内关键词检索：在（已 OCR 的）逐页文本里定位条款所在页与片段。
#
# 设计意图（对应 CLAUDE.md「search_in_docs」）：
#   扫描合同逐页 OCR 后，用关键词定位付款/违约/争议三类条款出现在第几页，
#   供 agent 判断“是否已拿到该找的条款、可以停止继续 OCR”。纯函数、可单测。
from __future__ import annotations

import re
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


# ── 按需 OCR 的「可以停了」判定：看条款正文特征，而不是关键词 ──────────────────────
# 关键词（付款/违约/争议）在目录页的章节标题里就会全部出现（实测 45 页合同在第 2 页目录即
# 误判"三条款已找到"而停止）。改用只在条款正文里出现的特征，并跳过目录页。
CLAUSE_BODY_PATTERNS: dict[str, re.Pattern[str]] = {
    # 付款：按合同总价的比例付款（「支付合同总价的 60%」「支付至合同总价的 95%」）或分项款名
    # 后跟冒号（「进度款：」）。排除「支付合同价款 10% 的违约金」「支付合同价的 2% 作为管理费」
    "payment": re.compile(
        r"(?:支付至?|付至|按)合同总价款?的?\s*\d+(?:\.\d+)?\s*%(?!\s*的?违约金)"
        r"|(?:进度款|验收款|预付款|质保金|尾款)\s*[:：]"
    ),
    # 违约：逾期付款对应的利息/违约金标准，或滞纳金
    "breach": re.compile(
        r"逾期(?:付款|支付)[^。；\n]{0,30}?(?:利息|违约金|万分之|%)|滞纳金"
    ),
    # 争议：向法院起诉 / 仲裁
    "dispute": re.compile(
        r"法院[^。；\n]{0,6}?(?:提起诉讼|起诉|诉讼)|仲裁委员会|提交[^。；\n]{0,10}?仲裁"
    ),
}
_CHAPTER_HEADING_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]+章")


def is_toc_page(text: str) -> bool:
    """目录页：含「目录」，或不同章标题密集（实测正文页 ≤5 个、目录续页 ≥10 个，取 8 为界）。"""
    compact = re.sub(r"\s+", "", text or "")
    return "目录" in compact or len(set(_CHAPTER_HEADING_RE.findall(compact))) >= 8


def locate_clause_bodies(pages: list[dict]) -> set[str]:
    """返回在非目录页中已出现条款正文特征的条款名集合（payment / breach / dispute）。"""
    found: set[str] = set()
    for pg in pages:
        text = pg.get("text") or ""
        if is_toc_page(text):
            continue
        for clause, pattern in CLAUSE_BODY_PATTERNS.items():
            if pattern.search(text):
                found.add(clause)
    return found
