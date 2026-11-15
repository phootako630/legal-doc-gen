# 金额提醒（只提醒、不改起诉金额）：合同总价与审批表合同总额不一致（确认单（七）第 4 题选 B）、
# 审批表「未付款总金额」多于未付金额（一体化合同审批表只列买卖部分）。虚构数据。
import pytest

from app.agent import nodes as nodes_mod
from app.services.complaint_readiness import readiness
from app.services.extraction import approval_unpaid_total
from app.services.validators import check_contract_total, check_unpaid_total, run_all_checks


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


def test_contract_total_mismatch_is_reminder_not_conflict():
    c = check_contract_total("683,972.35", "622200", has_supplement=False)
    assert c.needs_review and not c.is_conflict
    assert "622,200.00 元" in c.message and "683,972.35 元" in c.message and "61,772.35 元" in c.message
    assert "请核对" in c.message


@pytest.mark.parametrize(
    ("approval", "contract", "supplement"),
    [("622200", "622,200.00", False), (None, "622200", False), ("683972.35", "622200", True)],
)
def test_contract_total_no_reminder(approval, contract, supplement):
    assert not check_contract_total(approval, contract, supplement).needs_review


def test_integrated_contract_part_not_reminded():
    # 一体化合同：审批表只列安装款、合同总价 = 设备款 + 安装款，起诉状已写合计，不提醒
    assert not check_contract_total("384000", "1080000", False, (696000.0, 384000.0)).needs_review
    # 设备款 / 安装款没读到：照常提醒（起诉状只写了审批表的部分）
    assert check_contract_total("1,048,000.00", "1288000", False, (None, None)).needs_review


def test_contract_total_check_in_run_all_checks():
    fields = _f(total_amount="683972.35", contract_total_in_contract="622200")
    check = next(c for c in run_all_checks(fields) if c.key == "contract_total_match")
    assert check.needs_review
    # 提醒不算冲突，不拉低就绪度
    assert readiness(fields, run_all_checks(fields)) == readiness(fields, [])


@pytest.mark.parametrize(
    "text",
    [
        "类型买卖合同 签约台数8 账龄-货款欠款- 未付款总金额 64,400.00 买卖合同总额1,048,000.00 未付金额52,400.00",
        "| 实际履行台数 | 8 | 未付款总金额 | 64,400.00 |",
        "**实际履行台数** 8 **未付款总金额** 64,400.00 |",
    ],
)
def test_approval_unpaid_total_parsed(text):
    files = [{"filename": "审批表.pdf", "identified_type": "审批表", "text": text}]
    assert approval_unpaid_total(files) == 64400.0


def test_approval_unpaid_total_from_ocr_pages():
    files = [{"filename": "审批表.pdf", "identified_type": "审批表", "text": "",
              "pages": [{"page": 1, "text": "| 未付款总金额 | 1,000.00 |"}]}]
    assert approval_unpaid_total(files) == 1000.0
    assert approval_unpaid_total([{"identified_type": "合同", "text": "未付款总金额 5"}]) is None


def test_unpaid_total_reminder():
    c = check_unpaid_total(64400.0, "52400")
    assert c.needs_review and not c.is_conflict
    assert "64,400" in c.message and "52,400" in c.message and "12,000" in c.message
    assert not check_unpaid_total(52400.0, "52,400.00").needs_review
    assert not check_unpaid_total(None, "52400").needs_review


def test_all_checks_reads_approval_text():
    state = {"files": [{"identified_type": "审批表", "text": "未付款总金额64,400.00 未付金额52,400.00"}]}
    checks = nodes_mod._all_checks(state, _f(unpaid_amount=52400))
    check = next(c for c in checks if c.key == "unpaid_total_match")
    assert check.needs_review and check.related_fields == ["unpaid_amount"]
