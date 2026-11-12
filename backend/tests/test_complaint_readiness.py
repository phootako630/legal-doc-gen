# 就绪度与冲突字段的共用口径（#163 评审）：agent、案件 eval、快照统计调同一个函数，不各写一份。全部虚构数据。
from app.services.complaint_readiness import conflict_keys, readiness
from app.services.validators import ValidationCheck, check_to_dict, run_all_checks
from scripts.case_eval import snapshot_quality as sq

CHECKS = [
    ValidationCheck("amount_consistency", False, "金额不勾稽", ["total_amount", "paid_amount", "unpaid_amount"]),
    ValidationCheck("qty_consistency", False, "台数不一致", []),
    ValidationCheck("credit_code", True, "通过", ["defendant_credit_code"]),
    ValidationCheck("sign_date", False, "缺失", ["contract_sign_date"], applicable=False),  # 跳过的不算冲突
]


def test_conflict_keys_same_for_checks_and_dicts():
    expected = {"total_amount", "paid_amount", "unpaid_amount", "elevator_qty"}
    assert conflict_keys(CHECKS) == expected
    assert conflict_keys([check_to_dict(c) for c in CHECKS]) == expected  # 快照 / 接口里存的字典形式
    assert conflict_keys([]) == set() and conflict_keys(None) == set()


def test_readiness_drops_conflicted_fills():
    fields = {
        "total_amount": {"value": 1000.0, "src": "x"},
        "paid_amount": {"value": 600.0, "src": "x"},
        "unpaid_amount": {"value": 300.0, "src": "x"},
    }
    assert readiness(fields, CHECKS) < readiness(fields, [])
    assert readiness({}, []) == readiness({}, CHECKS)


def test_snapshot_export_readiness_uses_shared_function():
    final = {
        "total_amount": {"value": 1000.0, "src": "x"},
        "paid_amount": {"value": 600.0, "src": "x"},
        "unpaid_amount": {"value": 300.0, "src": "x"},
    }
    assert sq.final_readiness(final) == readiness(final, run_all_checks(final))
