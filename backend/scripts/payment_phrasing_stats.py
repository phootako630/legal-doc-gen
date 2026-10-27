"""付款条款写法统计（#116）：在 eval 案卷录制的 OCR 文本里数各种「支付……X%」写法，决定按需 OCR 要认哪些。

按需 OCR 每读一批页就用 doc_search.CLAUSE_BODY_PATTERNS["payment"] 判断付款条款找到没有，找到才停。
它只认「支付（至）/ 付至 / 按 + 合同总价（款）的 X%」等写法；合同写「合同价款的 50%」「本合同总价的 30%」
时认不出，会一直读到最后一页。本脚本统计真实材料里这些写法各出现多少次，供 #116 选定要覆盖的写法。

只读各案件 cache/ocr/ 下录制的 OCR 文本，不发任何请求。输出只有计数：不打印原文片段、不打印案件目录名
（目录名常是案件名），案件按顺序编号。比例前的名词只列预先写定的通用付款术语（KNOWN_BASES，如「合同价款」），
其余捕获到的名词一律只给种数和页数：同一主体常跨案件出现（「支付某某公司款项的50%」），按出现案件数放行也不安全（#123 评审）。

用法（在 backend/ 目录下；Docker 部署见 deploy/README.md「案件 eval」）：
    python scripts/payment_phrasing_stats.py /srv/eval-cases
    python scripts/payment_phrasing_stats.py eval_fixtures          # 虚构案卷集，试跑看格式
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.doc_search import CLAUSE_BODY_PATTERNS, is_toc_page  # noqa: E402

# 「支付 / 支付至 / 付至 / 付到 / 按 + 名词 + （的）+ 比例」：名词只取 2–8 个汉字，比例允许全角「％」。
_PAY_RATIO_RE = re.compile(
    r"(?:支付至|支付到|支付|付至|付到|按)\s*([一-龥]{2,8}?)\s*的?\s*\d+(?:\.\d+)?\s*([%％])"
)
# 分句边界：比例所在分句里出现违约金 / 滞纳金的不算付款，前后都查
# （「按合同价款的5%支付违约金」「逾期付款违约金按合同价款的5%计算」，#123 评审）
_CLAUSE_END = "。；;\n"
_PENALTY_RE = re.compile(r"违约金|滞纳金")
# #116 建议补认的名词（含「本合同总价」这类带「本」的写法）
CANDIDATE_BASES = (
    "合同价款", "合同价", "合同金额", "合同总额", "合同总金额", "合同总价款", "合同总价",
    "本合同总价", "本合同总价款", "本合同价款", "安装费", "安装总价", "安装款", "总价款", "结算价",
)
# 报告里允许列出名字的写法：上面的候选 + 现行已认的写法，都是通用付款术语，不含案件内容
KNOWN_BASES = frozenset(CANDIDATE_BASES)


def _clause(text: str, start: int, end: int) -> str:
    """start..end 所在的分句（到前后最近的句号 / 分号 / 换行为止）。"""
    left = max(text.rfind(ch, 0, start) for ch in _CLAUSE_END)
    rights = [i for i in (text.find(ch, end) for ch in _CLAUSE_END) if i != -1]
    return text[left + 1 : min(rights) if rights else len(text)]


def _payment_ratios(text: str) -> list[re.Match]:
    """页里的「支付……X%」，去掉所在分句是违约金 / 滞纳金的。"""
    return [m for m in _PAY_RATIO_RE.finditer(text) if not _PENALTY_RE.search(_clause(text, m.start(), m.end()))]


@dataclass
class CaseStats:
    pages: int = 0
    toc_pages: int = 0
    current_hits: int = 0  # 现行正则认得出付款条款正文的页
    ratio_pages: int = 0  # 出现「支付……X%」的页
    fullwidth_pages: int = 0  # 比例写成全角「％」的页
    candidate_hits: int = 0  # 补认候选写法后能认出的页（现行认不出的）
    bases: Counter = field(default_factory=Counter)  # 比例前名词 → 出现页数


def _case_dirs(root: Path) -> list[Path]:
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "cache" / "ocr").is_dir()
    )


def scan_case(case_dir: Path) -> CaseStats:
    stats = CaseStats()
    for path in sorted((case_dir / "cache" / "ocr").glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        stats.pages += 1
        if is_toc_page(text):
            stats.toc_pages += 1
            continue
        current = bool(CLAUSE_BODY_PATTERNS["payment"].search(text))
        stats.current_hits += current
        matches = _payment_ratios(text)
        if not matches:
            continue
        stats.ratio_pages += 1
        stats.fullwidth_pages += any(m.group(2) == "％" for m in matches)
        page_bases = {m.group(1) for m in matches}
        stats.bases.update(page_bases)
        if not current and page_bases & set(CANDIDATE_BASES):
            stats.candidate_hits += 1
    return stats


def render(cases: list[CaseStats]) -> str:
    total = CaseStats()
    case_count: Counter = Counter()  # 名词 → 出现在几个案件里
    for c in cases:
        for name in ("pages", "toc_pages", "current_hits", "ratio_pages", "fullwidth_pages", "candidate_hits"):
            setattr(total, name, getattr(total, name) + getattr(c, name))
        total.bases.update(c.bases)
        case_count.update(c.bases.keys())

    located_now = sum(1 for c in cases if c.current_hits)
    located_after = sum(1 for c in cases if c.current_hits or c.candidate_hits)
    lines = [
        "# 付款条款写法统计（#116）",
        "",
        f"案件 {len(cases)} 个，OCR 页 {total.pages} 页（其中目录页 {total.toc_pages} 页，不计）。",
        "",
        "## 按案件",
        "",
        "| 案件 | OCR 页 | 现行认得出的页 | 「支付……X%」页 | 全角「％」页 | 补认后新认出的页 |",
        "|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(cases, start=1):
        lines.append(
            f"| 案件{i} | {c.pages} | {c.current_hits} | {c.ratio_pages} | {c.fullwidth_pages} | {c.candidate_hits} |"
        )
    lines += [
        "",
        f"- 现行正则至少认出一页付款条款的案件：{located_now} / {len(cases)}",
        f"- 加上候选写法后：{located_after} / {len(cases)}",
        f"- 比例写成全角「％」的页：{total.fullwidth_pages}（现行正则只认半角「%」）",
        "",
        "## 比例前的写法（出现页数）",
        "",
        "只列通用付款术语；★ 为 #116 候选，✓ 为现行已认。其他写法不列名字，只给种数和页数。",
        "",
        "| 写法 | 页数 | 案件数 | |",
        "|---|---|---|---|",
    ]
    other_pages = other_kinds = 0
    for base, n in total.bases.most_common():
        if base not in KNOWN_BASES:
            other_pages += n
            other_kinds += 1
            continue
        mark = "✓" if re.fullmatch(r"合同总价款?", base) else "★"
        lines.append(f"| {base} | {n} | {case_count[base]} | {mark} |")
    lines += ["", f"其他写法：{other_kinds} 种，共 {other_pages} 页（不列名字）。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统计 eval 案卷 OCR 文本里的付款条款写法（只输出计数）")
    parser.add_argument("root", type=Path, help="案卷根目录，如 /srv/eval-cases")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"找不到目录：{args.root}", file=sys.stderr)
        return 2
    dirs = _case_dirs(args.root)
    if not dirs:
        print("没有找到带 cache/ocr/ 的案件（先跑一次 eval_cases.py 录制 OCR）", file=sys.stderr)
        return 2
    print(render([scan_case(d) for d in dirs]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
