# eval 打分：系统最终字段与 expected.json 的标准值逐字段比对（确定性，不用 LLM 判）。
#
# 按字段比对，不逐句比对诉状全文：律师之间的措辞本就不统一，全文比对会把措辞差异算成错误。
# expected.json 的写法见 scripts/eval_cases.py 文件头。
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.services.validators import parse_amount, parse_date

MATCHES = ("text", "contains", "amount", "date", "absent")

# 结论
CORRECT = "正确"
WRONG = "错误"
MISSING = "缺失"  # 应有值，系统留空（起诉状里是【待补充】）
PENDING = "待定"  # 标准值待律师确认，不计分

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "verdict": self.verdict,
            "expected": self.expected,
            "actual": self.actual,
            "match": self.match,
            "src": self.src,
            "note": self.note,
        }


def _norm_text(raw: Any) -> str:
    # 全角 / 半角、空白不算差异（OCR 与律师手写在这些地方常不一致）
    text = unicodedata.normalize("NFKC", str(raw))
    return re.sub(r"\s+", "", text)


def _blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def parse_spec(spec: Any) -> dict[str, Any]:
    """把 expected.json 的一项展开成 {value, match, note, pending}；简写按值的类型推断比对方式。"""
    if not isinstance(spec, dict):
        spec = {"value": spec}
    spec = dict(spec)
    if "pending" in spec:
        return {"value": spec.get("value"), "match": "text", "note": str(spec["pending"]), "pending": True}
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
    return {"value": value, "match": match, "note": str(spec.get("note") or ""), "pending": False}


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


def score_field(key: str, spec: Any, node: Any) -> FieldResult:
    """比对单个字段。node 为系统的 FieldValue（dict）或缺失。"""
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
    return FieldResult(key, verdict, expected, actual, match, src, note)


def score_case(expected_fields: dict[str, Any], actual_fields: dict[str, Any]) -> list[FieldResult]:
    return [score_field(key, spec, actual_fields.get(key)) for key, spec in expected_fields.items()]


def summarize(results: list[FieldResult]) -> dict[str, int]:
    counts = {CORRECT: 0, WRONG: 0, MISSING: 0, PENDING: 0}
    for r in results:
        counts[r.verdict] += 1
    return counts
