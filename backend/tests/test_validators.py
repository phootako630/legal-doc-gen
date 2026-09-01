# validators.py 单测：金额勾稽 / 台数三源 / 信用代码校验位 / 日期 / 批量入口
import pytest

from app.services.validators import (
    ValidationCheck,
    check_amounts,
    check_date_valid,
    check_elevator_qty,
    latest_acceptance_date,
    parse_amount,
    parse_date,
    parse_qty,
    run_all_checks,
    validate_credit_code,
)

# 已知合法的统一社会信用代码（GB 32100-2015 常用样例，末位校验位 = 3）
VALID_USCC = "91350100M000100Y43"


# ── 解析辅助 ──────────────────────────────────────────────────────────────
def test_parse_amount_variants():
    assert parse_amount("894,934.00") == 894934.0
    assert parse_amount("￥638667.2 元") == pytest.approx(638667.2)
    assert parse_amount(256266.8) == pytest.approx(256266.8)
    assert parse_amount(None) is None
    assert parse_amount("无") is None


def test_parse_qty_variants():
    assert parse_qty("6台") == 6
    assert parse_qty("6.0") == 6
    assert parse_qty(6) == 6
    assert parse_qty("6.5") is None  # 非整数台数视为无法解析
    assert parse_qty(None) is None


def test_parse_date_validity():
    assert parse_date("2023年5月1日") == (2023, 5, 1)
    assert parse_date("2023-02-29") is None  # 平年 2 月无 29 日
    assert parse_date("2024-02-29") == (2024, 2, 29)  # 闰年合法
    assert parse_date("2023-13-01") is None  # 非法月份


# ── 金额勾稽 ──────────────────────────────────────────────────────────────
def test_amount_reconcile_pass_real_sample():
    # CLAUDE.md 真实样例：638667.2 + 256266.8 == 894934
    c = check_amounts("894934", "638667.2", "256266.8")
    assert c.passed is True
    assert c.applicable is True
    assert c.is_conflict is False


def test_amount_reconcile_conflict():
    c = check_amounts(900000, 638667.2, 256266.8)
    assert c.passed is False
    assert c.is_conflict is True


def test_amount_reconcile_not_applicable_when_missing():
    c = check_amounts(894934, None, 256266.8)
    assert c.applicable is False
    assert c.is_conflict is False  # 缺失 ≠ 冲突


def test_amount_cent_tolerance():
    c = check_amounts(100.00, 33.33, 66.67)
    assert c.passed is True


# ── 台数三源 ──────────────────────────────────────────────────────────────
def test_qty_all_three_consistent():
    c = check_elevator_qty("6", "6台", 6)
    assert c.passed is True


def test_qty_conflict():
    c = check_elevator_qty(6, 6, 5)
    assert c.passed is False
    assert c.is_conflict is True


def test_qty_two_sources_enough():
    c = check_elevator_qty(6, None, 6)
    assert c.applicable is True
    assert c.passed is True


def test_qty_single_source_not_applicable():
    c = check_elevator_qty(6, None, None)
    assert c.applicable is False
    assert c.is_conflict is False


# ── 统一社会信用代码 ──────────────────────────────────────────────────────
def test_credit_code_valid():
    c = validate_credit_code(VALID_USCC)
    assert c.passed is True
    assert c.applicable is True


def test_credit_code_bad_checksum():
    # 篡改末位 → 校验位不符
    bad = VALID_USCC[:-1] + ("4" if VALID_USCC[-1] != "4" else "5")
    c = validate_credit_code(bad)
    assert c.passed is False
    assert c.is_conflict is True


def test_credit_code_wrong_length():
    c = validate_credit_code("9135010")
    assert c.passed is False
    assert "位数" in c.message


def test_credit_code_illegal_charset():
    # 含被排除字符 I/O/S/V/Z 之一
    c = validate_credit_code("91350100I000100Y43")
    assert c.passed is False


def test_credit_code_missing_not_applicable():
    c = validate_credit_code(None)
    assert c.applicable is False
    assert c.is_conflict is False


# ── 日期 ──────────────────────────────────────────────────────────────────
def test_date_valid_and_invalid():
    assert check_date_valid("2023年5月1日").passed is True
    assert check_date_valid("2023-02-30").passed is False
    assert check_date_valid(None).applicable is False


def test_latest_acceptance_date():
    dates = ["2023-05-01", "2023年6月15日", "2023/04/20", None, "乱码"]
    assert latest_acceptance_date(dates) == (2023, 6, 15)
    assert latest_acceptance_date(["无", None]) is None


# ── 批量入口 ──────────────────────────────────────────────────────────────
def test_run_all_checks_on_extracted_fields():
    fields = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "elevator_qty_by_approval": {"value": 6, "src": "《审批表》"},
        "elevator_qty_by_contract": {"value": 6, "src": "《合同》"},
        "elevator_qty_by_acceptance": {"value": 6, "src": "《验收报告》"},
        "defendant_credit_code": {"value": VALID_USCC, "src": "《验收报告》"},
        "contract_sign_date": {"value": "2023年5月1日", "src": "《审批表》"},
    }
    checks = run_all_checks(fields)
    assert all(isinstance(c, ValidationCheck) for c in checks)
    assert all(c.passed for c in checks)
    assert not any(c.is_conflict for c in checks)
