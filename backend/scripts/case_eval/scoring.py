# eval 打分：系统最终字段与 expected.json 的标准值逐字段比对（确定性，不用 LLM 判）。
#
# 按字段比对，不逐句比对诉状全文：律师之间的措辞本就不统一，全文比对会把措辞差异算成错误。
# expected.json 的写法见 scripts/eval_cases.py 文件头。
#
# 除了值，还看两件事（#59）：
#   - 来源：标准答案填了 source 时，把系统值回原文重新锚定（与应用同一个 resolve_provenance），
#     看实际在哪个文件命中，不看模型在 src 里自称来自哪里
#   - 静默错误：审核页显示「正常」（判定与前端 buildReviewFields 一致），但值错了、
#     标准答案要求待核实，或回原文没命中——律师最容易漏看的一类错
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.services.provenance import resolve_provenance
from app.services.validators import parse_amount, parse_date

MATCHES = ("text", "contains", "amount", "date", "absent")

# 结论
CORRECT = "正确"
WRONG = "错误"
MISSING = "缺失"  # 应有值，系统留空或值里带【待补充】（起诉状里是【待补充】）
PENDING = "待定"  # 标准值待律师确认，不计分

# 来源 / 静默错误计数（不影响上面四类结论与准确率口径）
SOURCE_WRONG = "来源错误"  # 填了 source 的字段，值回原文命中的文件不对，或哪个文件都没命中
SILENT = "静默错误"  # 显示「正常」，但值错 / 应标待核实 / 回原文未命中

# 审核页显示的状态（与前端 buildReviewFields 的判定一致）
SHOWN_NORMAL = "正常"
SHOWN_REVIEW = "待核实"
SHOWN_CONFLICT = "冲突"
SHOWN_MISSING = "缺失"
REVIEWS = (SHOWN_REVIEW,)

_FULL_DATE_RE = re.compile(r"^\s*\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*[日号]?\s*$")


@dataclass
class FieldResult:
    key: str
    verdict: str
    expected: Any
    actual: Any
    match: str
    src: str = ""  # 系统取值的出处：看错在抽取（文件出处）还是规则（「按律师规则」）
    note: str = ""  # expected.json 里的说明，或「待定」的原因
    shown: str = ""  # 审核页显示的状态
    expected_source: str | None = None
    verified_source: str | None = None  # 值回原文实际命中的文件（只在填了 source 时算）
    source_ok: bool | None = None  # None = 没检查来源
    silent: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "verdict": self.verdict,
            "expected": self.expected,
            "actual": self.actual,
            "match": self.match,
            "src": self.src,
            "note": self.note,
            "shown": self.shown,
            "expected_source": self.expected_source,
            "verified_source": self.verified_source,
            "source_ok": self.source_ok,
            "silent": self.silent,
        }


def _norm_text(raw: Any) -> str:
    # 全角 / 半角、空白不算差异（OCR 与律师手写在这些地方常不一致）
    text = unicodedata.normalize("NFKC", str(raw))
    return re.sub(r"\s+", "", text)


# 派生值里拼着【待补充】（如「【待补充】人民法院」）：起诉状里同样是待补充，算缺失而不是取错
_TO_FILL = "【待补充】"


def _blank(raw: Any) -> bool:
    if raw is None:
        return True
    return isinstance(raw, str) and (not raw.strip() or _TO_FILL in raw)


def parse_spec(spec: Any) -> dict[str, Any]:
    """把 expected.json 的一项展开成 {value, match, note, pending, source, review}；
    简写按值的类型推断比对方式。source / review 选填，不填时只比值（旧 expected.json 照常可用）。"""
    if not isinstance(spec, dict):
        spec = {"value": spec}
    spec = dict(spec)
    source = spec.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise ValueError("source 应为预期来源文件名（或其中一段），如「安装合同」")
    review = spec.get("review")
    if review is not None and review not in REVIEWS:
        raise ValueError(f"review 只能写：{'/'.join(REVIEWS)}")
    extra = {"source": source, "review": review}
    if "pending" in spec:
        return {"value": spec.get("value"), "match": "text", "note": str(spec["pending"]),
                "pending": True, **extra}
    value = spec.get("value")
    match = spec.get("match")
    if match is None:
        if value is None:
            match = "absent"
        elif isinstance(value, (int, float)):
            match = "amount"
        elif isinstance(value, str) and _FULL_DATE_RE.match(value):
            match = "date"
        else:
            match = "text"
    if match not in MATCHES:
        raise ValueError(f"未知比对方式：{match}（可选 {'/'.join(MATCHES)}）")
    return {"value": value, "match": match, "note": str(spec.get("note") or ""), "pending": False,
            **extra}


def _equal(match: str, expected: Any, actual: Any) -> bool:
    if match == "amount":
        a, b = parse_amount(expected), parse_amount(actual)
        return a is not None and b is not None and abs(a - b) < 0.005
    if match == "date":
        a, b = parse_date(expected), parse_date(actual)
        return a is not None and a == b
    if match == "contains":
        return _norm_text(expected) in _norm_text(actual)
    return _norm_text(expected) == _norm_text(actual)


def shown_status(key: str, node: Any, conflict_keys: set[str] | frozenset[str] = frozenset()) -> str:
    """审核页上该字段显示的状态，判定与前端 buildReviewFields.inferStatus 一致。"""
    if key in conflict_keys:
        return SHOWN_CONFLICT
    value = node.get("value") if isinstance(node, dict) else None
    if value is None:
        return SHOWN_MISSING
    src = str(node.get("src") or "").lower()
    if "冲突" in src or "不一致" in src:
        return SHOWN_CONFLICT
    if "ocr" in src or "扫描" in src or "待核实" in src:
        return SHOWN_REVIEW
    return SHOWN_NORMAL


def score_field(
    key: str,
    spec: Any,
    node: Any,
    files: list[dict] | None = None,
    conflict_keys: set[str] | frozenset[str] = frozenset(),
) -> FieldResult:
    """比对单个字段。node 为系统的 FieldValue（dict）或缺失；files 为分析结束时的材料
    （含 OCR 补读的文字），填了 source 时用来回原文核对来源。"""
    parsed = parse_spec(spec)
    actual = node.get("value") if isinstance(node, dict) else None
    src = str(node.get("src") or "") if isinstance(node, dict) else ""
    expected, match, note = parsed["value"], parsed["match"], parsed["note"]

    if parsed["pending"]:
        verdict = PENDING
    elif match == "absent":
        verdict = CORRECT if _blank(actual) else WRONG
    elif _blank(actual):
        verdict = MISSING
    else:
        verdict = CORRECT if _equal(match, expected, actual) else WRONG
    result = FieldResult(key, verdict, expected, actual, match, src, note)
    if parsed["pending"]:
        return result  # 待定字段只列出，来源与复核状态也不计
    result.shown = shown_status(key, node, conflict_keys)

    unanchored = False
    if parsed["source"] and not _blank(actual):
        # 来源以回原文实际命中的文件为准（与应用锚定同一套逻辑），不信 src 里写的文件名
        prov = resolve_provenance(actual, src, files or [])
        result.expected_source = parsed["source"]
        result.verified_source = prov.filename
        unanchored = prov.filename is None
        result.source_ok = not unanchored and _norm_text(parsed["source"]) in _norm_text(prov.filename)

    if result.shown == SHOWN_NORMAL:
        result.silent = verdict == WRONG or parsed["review"] == SHOWN_REVIEW or unanchored
    return result


def score_case(
    expected_fields: dict[str, Any],
    actual_fields: dict[str, Any],
    files: list[dict] | None = None,
    conflict_keys: set[str] | frozenset[str] = frozenset(),
) -> list[FieldResult]:
    return [
        score_field(key, spec, actual_fields.get(key), files, conflict_keys)
        for key, spec in expected_fields.items()
    ]


def summarize(results: list[FieldResult]) -> dict[str, int]:
    counts = {CORRECT: 0, WRONG: 0, MISSING: 0, PENDING: 0}
    for r in results:
        counts[r.verdict] += 1
    return counts


def summarize_quality(results: list[FieldResult]) -> dict[str, int]:
    """来源错误、静默错误的数量，以及检查了来源的字段数（报告里「来源错误 1/5」）。"""
    return {
        SOURCE_WRONG: sum(1 for r in results if r.source_ok is False),
        "来源检查": sum(1 for r in results if r.source_ok is not None),
        SILENT: sum(1 for r in results if r.silent),
    }
