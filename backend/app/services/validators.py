# ② 确定性交叉校验：金额勾稽 / 台数三源一致 / 信用代码校验位 / 日期合法性
#
# 设计意图（对应 CLAUDE.md「校验用代码，不用 LLM」）：
#   所有可量化的一致性判断都在这里用确定性 Python 完成，LLM 只负责"抽出数字"，
#   "比对"由本模块算。每个检查返回 ValidationCheck，供 confidence/状态映射与 UI 使用。
#
#   applicable 字段区分"检查不适用（输入缺失，无法判断）"与"检查过且失败（真冲突）"，
#   这样上层能把「缺失」与「冲突」分开——缺失走 missing，冲突才是最高优先级的 conflict。
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── 结果模型 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationCheck:
    """单项交叉校验结果。message 为面向律师的中文说明。"""

    key: str
    passed: bool
    message: str
    related_fields: list[str] = field(default_factory=list)
    # applicable=False 表示输入不足、本次检查跳过（既非通过也非冲突）
    applicable: bool = True

    @property
    def is_conflict(self) -> bool:
        """真冲突 = 适用且未通过。缺失场景 applicable=False，不算冲突。"""
        return self.applicable and not self.passed


# ── 解析辅助 ──────────────────────────────────────────────────────────────────


def parse_amount(raw: object) -> float | None:
    """把金额（可能带千分位/货币符号/单位）解析为浮点数，失败返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[，,\s]", "", unicodedata.normalize("NFKC", str(raw)))
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_qty(raw: object) -> int | None:
    """把台数解析为整数（容忍 '6台' / '6.0'），失败返回 None。"""
    num = parse_amount(raw)
    if num is None:
        return None
    if abs(num - round(num)) > 1e-9:
        return None
    return int(round(num))


_DATE_RE = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?")
_DAYS_IN_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def parse_date(raw: object) -> tuple[int, int, int] | None:
    """解析常见中文/ISO 日期为 (年,月,日) 并校验合法性，失败/非法返回 None。"""
    if raw is None:
        return None
    m = _DATE_RE.search(unicodedata.normalize("NFKC", str(raw)))
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12):
        return None
    max_day = _DAYS_IN_MONTH[month - 1]
    # 2 月闰年判断
    if month == 2 and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        max_day = 28
    if not (1 <= day <= max_day):
        return None
    return (year, month, day)


# ── 金额勾稽：总额 == 已付 + 未付 ────────────────────────────────────────────


def check_amounts(
    total: object, paid: object, unpaid: object
) -> ValidationCheck:
    """金额勾稽：合同总价 == 已付 + 未付（按分容差）。缺任一项则不适用。"""
    key = "amount_reconcile"
    related = ["total_amount", "paid_amount", "unpaid_amount"]
    t, p, u = parse_amount(total), parse_amount(paid), parse_amount(unpaid)

    if t is None or p is None or u is None:
        return ValidationCheck(
            key, False, "金额字段不完整，无法进行勾稽校验。", related, applicable=False
        )

    if abs(t - (p + u)) <= 0.01:
        return ValidationCheck(
            key,
            True,
            f"金额勾稽通过：总价 {t:g} = 已付 {p:g} + 未付 {u:g}。",
            related,
        )
    return ValidationCheck(
        key,
        False,
        f"金额勾稽不一致：总价 {t:g} ≠ 已付 {p:g} + 未付 {u:g}（差 {t - (p + u):g}）。",
        related,
    )


# ── 台数三源一致：签约(审批) / 合同 / 验收 ──────────────────────────────────


def check_elevator_qty(
    by_approval: object, by_contract: object, by_acceptance: object
) -> ValidationCheck:
    """台数三源比对：审批表 / 合同 / 验收报告口径应一致。少于两源则不适用。"""
    key = "qty_consistency"
    related = [
        "elevator_qty_by_approval",
        "elevator_qty_by_contract",
        "elevator_qty_by_acceptance",
    ]
    sources = {
        "审批表": parse_qty(by_approval),
        "合同": parse_qty(by_contract),
        "验收报告": parse_qty(by_acceptance),
    }
    present = {name: qty for name, qty in sources.items() if qty is not None}

    if len(present) < 2:
        return ValidationCheck(
            key, False, "可比对的台数来源不足两个，暂不校验。", related, applicable=False
        )

    distinct = set(present.values())
    if len(distinct) == 1:
        qty = distinct.pop()
        srcs = "、".join(present.keys())
        return ValidationCheck(
            key, True, f"台数一致：{srcs}均为 {qty} 台。", related
        )
    detail = "；".join(f"{name} {qty} 台" for name, qty in present.items())
    return ValidationCheck(
        key, False, f"台数不一致：{detail}。请律师核实以哪一口径为准。", related
    )


# ── 统一社会信用代码：18 位 + 字符集 + 校验位（GB 32100-2015）──────────────

# 代码字符集（不含 I O S V Z），下标即字符权值
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_CHAR_INDEX = {c: i for i, c in enumerate(_USCC_CHARS)}
# 前 17 位加权因子（3^i mod 31）
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
_USCC_RE = re.compile(r"^[0-9A-HJ-NP-RT-Y]{18}$")


def _uscc_check_char(body17: str) -> str | None:
    """按标准算法计算第 18 位校验字符；body17 含非法字符返回 None。"""
    total = 0
    for ch, w in zip(body17, _USCC_WEIGHTS):
        idx = _USCC_CHAR_INDEX.get(ch)
        if idx is None:
            return None
        total += idx * w
    remainder = total % 31
    check_val = (31 - remainder) % 31
    return _USCC_CHARS[check_val]


def validate_credit_code(code: object) -> ValidationCheck:
    """统一社会信用代码：18 位、字符集合法、校验位正确。空值不适用。"""
    key = "credit_code"
    related = ["defendant_credit_code"]

    if code is None or str(code).strip() == "":
        return ValidationCheck(
            key, False, "统一社会信用代码缺失，无法校验。", related, applicable=False
        )

    text = str(code).strip().upper()
    if len(text) != 18:
        return ValidationCheck(
            key, False, f"统一社会信用代码位数错误（应为 18 位，实为 {len(text)} 位）。", related
        )
    if not _USCC_RE.match(text):
        return ValidationCheck(
            key, False, "统一社会信用代码含非法字符（不含 I、O、S、V、Z）。", related
        )

    expected = _uscc_check_char(text[:17])
    if expected is not None and expected == text[17]:
        return ValidationCheck(key, True, "统一社会信用代码校验通过。", related)
    return ValidationCheck(
        key,
        False,
        f"统一社会信用代码校验位不符（末位应为 {expected}，实为 {text[17]}）。",
        related,
    )


# ── 日期合法性与最晚验收日 ──────────────────────────────────────────────────


def check_date_valid(date_value: object, field_key: str = "contract_sign_date") -> ValidationCheck:
    """日期合法性：能否解析为合法年月日。空值不适用。"""
    key = "date_valid"
    if date_value is None or str(date_value).strip() == "":
        return ValidationCheck(
            key, False, "日期缺失，无法校验。", [field_key], applicable=False
        )
    parsed = parse_date(date_value)
    if parsed is None:
        return ValidationCheck(
            key, False, f"日期无法解析或非法：{date_value}。", [field_key]
        )
    y, m, d = parsed
    return ValidationCheck(key, True, f"日期合法：{y}年{m}月{d}日。", [field_key])


def latest_acceptance_date(dates: list[object]) -> tuple[int, int, int] | None:
    """从多份验收报告的合格日期中取最晚者（acceptance_latest_date 计算依据）。"""
    parsed = [p for p in (parse_date(d) for d in dates) if p is not None]
    if not parsed:
        return None
    return max(parsed)


# ── 批量入口：从 extracted_fields 跑全部检查 ────────────────────────────────


def _field_value(fields: dict, key: str) -> object:
    """从 extracted_fields[key] 取 value；兼容 {value,src} 结构与裸值。"""
    node = fields.get(key)
    if isinstance(node, dict):
        return node.get("value")
    return node


def run_all_checks(fields: dict) -> list[ValidationCheck]:
    """对一份 extracted_fields 运行全部确定性校验，返回结果列表。"""
    checks = [
        check_amounts(
            _field_value(fields, "total_amount"),
            _field_value(fields, "paid_amount"),
            _field_value(fields, "unpaid_amount"),
        ),
        check_elevator_qty(
            _field_value(fields, "elevator_qty_by_approval"),
            _field_value(fields, "elevator_qty_by_contract"),
            _field_value(fields, "elevator_qty_by_acceptance"),
        ),
        validate_credit_code(_field_value(fields, "defendant_credit_code")),
        check_date_valid(_field_value(fields, "contract_sign_date"), "contract_sign_date"),
    ]
    return checks
