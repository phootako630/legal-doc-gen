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
    # 争议：向法院起诉 / 仲裁；或主合同把争议解决交给战略 / 框架合同（#126：找到这句也算争议条款已找到）
    "dispute": re.compile(
        r"法院[^。；\n]{0,6}?(?:提起诉讼|起诉|诉讼)|仲裁委员会|提交[^。；\n]{0,10}?仲裁"
        r"|争议[^。；;]{0,40}?按\s*[“\"「『]?\s*(?:战略|框架)(?:合同|协议)"
    ),
    # 质保期：质保金按日违约金的起算日 = 验收 + 质保期的次日（补充答复 Q6）
    "warranty": re.compile(
        r"(?:质保|保修)期[^。；\n]{0,15}?(?:[0-9]+|[一二两三四五六七八九十]+)\s*个?\s*(?:年|月)"
    ),
    # 签章页：签约日期与合同乙方以此页为准（律师确认单第 4 题、补充第 1 题）。
    # 认「（以下无正文）」「签订日期：」「甲方（盖章）」这类只在签章页出现的写法
    "signature": re.compile(
        r"以下无正文|(?:签订|签约|签署)日期\s*[:：]|[甲乙]方\s*[（(]\s*(?:盖章|公章|签章)"
    ),
}
_CHAPTER_HEADING_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]+章")


def is_toc_page(text: str) -> bool:
    """目录页：含「目录」，或不同章标题密集（实测正文页 ≤5 个、目录续页 ≥10 个，取 8 为界）。"""
    compact = re.sub(r"\s+", "", text or "")
    return "目录" in compact or len(set(_CHAPTER_HEADING_RE.findall(compact))) >= 8


def locate_clause_bodies(pages: list[dict]) -> set[str]:
    """返回在非目录页中已出现正文特征的目标集合（payment / breach / dispute / warranty / signature）。"""
    found: set[str] = set()
    for pg in pages:
        text = pg.get("text") or ""
        if is_toc_page(text):
            continue
        for clause, pattern in CLAUSE_BODY_PATTERNS.items():
            if pattern.search(text):
                found.add(clause)
    return found


# 主合同只写「争议的解决……按战略合同内相关条款执行」，本身没有约定仲裁 / 法院（律师反馈：徐州苏宁；#126）
STRATEGIC_REF_RE = re.compile(r"(?:战略|框架)(?:合同|协议|合作)")
FORUM_RE = re.compile(r"仲裁|法院|诉讼|管辖")
# 主合同里把争议解决交给战略合同的那句（常写在「合同的组成及解释」列表里，不在争议解决章节）
# 文字版 PDF 长句会折行，中间允许换行
STRATEGIC_DISPUTE_REF_RE = re.compile(r"争议[^。；;\x00]{0,40}?按\s*[“\"「『]?\s*(?:战略|框架)(?:合同|协议)")


# 转引那一段本身（「按双方签订的《战略合作协议》约定执行」「按战略合同关于管辖的条款执行」）：
# 里面提到的「管辖 / 仲裁」说的是战略合同的条款，不是主合同自己的约定（#133 评审）
_STRATEGIC_REF_SPAN_RE = re.compile(
    r"(?:按|依|依据|按照|适用)[^。；;]{0,20}?(?:战略|框架)(?:合同|协议|合作)[^。；;]{0,30}?(?:执行|办理|处理|约定|为准)"
)


def refers_to_strategic(text: str | None) -> bool:
    """争议条款只是转引战略 / 框架合同，主合同自己没写仲裁机构或法院（转引段里的字眼不算）。"""
    compact = re.sub(r"\s+", "", text or "")
    if not STRATEGIC_REF_RE.search(compact):
        return False
    return not FORUM_RE.search(_STRATEGIC_REF_SPAN_RE.sub("", compact))
