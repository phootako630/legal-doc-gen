# 诉请金额（确认单（六）第 1 题选 A，#138）：质保期未届满时只诉已到期部分 = 合同总价 × 支付至比例 − 已付。虚构数据。
from datetime import date

from app.services import derived_fields
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields


def _case(ratio_src="律师确认", ratio="95%", unpaid="100,000.00", **extra):
    fields = {
        "total_amount": {"value": "1,000,000.00", "src": "《虚构审批表》"},
        "paid_amount": {"value": "900,000.00", "src": "《虚构审批表》"},
        "unpaid_amount": {"value": unpaid, "src": "《虚构审批表》未付款金额"},
        "retention_unpaid_amount": {"value": "50,000.00", "src": "《虚构审批表》"},
        "acceptance_latest_date": {"value": "2099年1月10日", "src": "《虚构验收报告》"},
        "warranty_period": {"value": "24个月", "src": "《虚构合同》质保期自验收合格之日起24个月"},
    }
    if ratio is not None:
        fields["payable_ratio"] = {"value": ratio, "src": ratio_src}
    fields.update(extra)
    return fields


def test_retention_not_due_claims_only_due_part(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    f = apply_derived_fields(_case())
    claim = f["claim_amount"]
    assert claim["value"] == "50000"
    assert "合同总价 × 95% − 已付" in claim["src"] and "待核实" in claim["src"]
    assert "已于" not in claim["src"]  # 质保期还没届满，不提示核对比例
    # 审批表未付款原值不动（金额勾稽仍按它），下游措辞 / 算式 / 应付金额按诉请金额
    assert f["unpaid_amount"]["value"] == "100,000.00"
    assert f["debt_term"]["value"] == "到期合同款"
    assert f["unpaid_note"]["value"] == "（950000-900000）"
    assert f["payable_amount"]["value"] == "950000"


def test_complaint_claims_and_interest_base_use_claim(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    text = render_complaint(_case())
    assert "支付到期合同款¥⚠️ 待核实：50000元" in text
    assert text.count("以¥⚠️ 待核实：50000元为基数") == 2  # 诉请第 2 项与事实理由
    assert "100000" not in text  # 审批表未付款（含未到期质保金）不写进起诉状


def test_warranty_already_expired_claims_approval_unpaid(monkeypatch):
    # 质保期已届满：质保金已到期，即使律师选了支付至 95% 也按审批表未付款全额（#143 评审）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2102, 1, 1))
    claim = apply_derived_fields(_case())["claim_amount"]
    assert claim["value"] == "100000"
    assert "已于 2101年1月10日 届满" in claim["src"] and "全额" in claim["src"]
    assert "《虚构验收报告》" in claim["src"]


def test_computed_claim_cites_input_sources(monkeypatch):
    # 算出的诉请金额带上参与计算各字段的出处，律师能追到材料（#143 评审）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    src = apply_derived_fields(_case())["claim_amount"]["src"]
    for cited in ("合同总价：《虚构审批表》", "未付款：《虚构审批表》未付款金额", "最晚验收合格日：《虚构验收报告》",
                  "质保期：《虚构合同》"):
        assert cited in src


def test_no_due_amount_left_for_lawyer(monkeypatch):
    # 未付款全部是未到期质保金：本次没有到期欠款，不自动写正额诉请，交律师确认（#143 评审）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(unpaid="50,000.00", paid_amount={"value": "950,000.00", "src": "《虚构审批表》"})
    claim = apply_derived_fields(fields)["claim_amount"]
    assert claim["value"] is None
    assert "本次没有到期欠款" in claim["src"] and "请律师确认" in claim["src"]
    assert "【待补充】" in render_complaint(fields)


def test_no_due_amount_without_ratio(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(ratio=None, unpaid="50,000.00", paid_amount={"value": "950,000.00", "src": "《虚构审批表》"})
    claim = apply_derived_fields(fields)["claim_amount"]
    assert claim["value"] is None and "本次没有到期欠款" in claim["src"]


def test_fact_sentence_uses_debt_term(monkeypatch):
    # 事实段与诉请第 1 项同一措辞：只诉到期部分时写「尚欠到期合同款」，不写「剩余合同款」（#143 评审）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    text = render_complaint(_case())
    assert "尚欠到期合同款¥⚠️ 待核实：50000元" in text
    assert "剩余合同款" not in text


def test_full_payment_ratio_keeps_approval_unpaid():
    f = apply_derived_fields(_case(ratio="100%"))
    assert f["claim_amount"]["value"] == "100,000.00"
    assert f["claim_amount"]["src"] == "《虚构审批表》未付款金额"


def test_unpaid_already_equals_due_part_unchanged():
    # 审批表未付款本来就只含到期部分（已付 + 未付 = 总价 × 95%）：照用审批表
    f = apply_derived_fields(_case(unpaid="50,000.00"))
    assert f["claim_amount"]["value"] == "50,000.00"
    assert f["debt_term"]["value"] == "到期合同款"


def test_lawyer_edited_claim_is_kept(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(claim_amount={"value": "60000", "src": "律师人工确认修改"})
    f = apply_derived_fields(fields)
    assert f["claim_amount"]["value"] == "60000"
    assert f["payable_amount"]["value"] == "960000"


def test_penalty_on_unpaid_uses_claim(monkeypatch):
    # 一次性违约金「欠款的5%」：基数是本次诉请的欠款（只含已到期部分）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(
        breach_clause_text={"value": "甲方逾期付款的，应向乙方支付欠款的5%作为违约金。", "src": "《虚构合同》"},
    )
    f = apply_derived_fields(fields)
    assert f["penalty_amount"]["value"] == 2500


_STAGES = "3.1 电梯验收合格后30日内，甲方支付至合同总价的95%。3.2 余款5%为质保金，质保期满后无息返还。"


def test_stage_match_uses_due_part_when_retention_not_due(monkeypatch):
    # 审批表已付 + 未付 = 总价（未付含 5% 质保金），质保期还没届满：不再按 100% 匹配，按 95% 节点（#138）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(ratio=None, payment_clause_text={"value": _STAGES, "src": "《虚构合同》第三条"})
    f = apply_derived_fields(fields)
    assert f["payable_ratio"]["value"] == "95%"
    assert f["claim_amount"]["value"] == "50000"
    assert f["payment_cite_location"]["value"] == "第3.1条"  # 只引用到期的验收款节点，不引用质保金节点


def test_stage_match_full_once_warranty_expired(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2102, 1, 1))
    fields = _case(ratio=None, payment_clause_text={"value": _STAGES, "src": "《虚构合同》第三条"})
    f = apply_derived_fields(fields)
    assert f["payable_ratio"]["value"] == "100%"
    assert f["claim_amount"]["value"] == "100000"
    assert "届满" in f["claim_amount"]["src"]


def test_unknown_warranty_keeps_approval_unpaid(monkeypatch):
    # 质保期读不到：推不出是否到期，照旧按审批表（付款节点按 100% 匹配）
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(ratio=None, payment_clause_text={"value": _STAGES, "src": "《虚构合同》第三条"})
    fields["warranty_period"] = {"value": None, "src": "未识别"}
    f = apply_derived_fields(fields)
    assert f["payable_ratio"]["value"] == "100%"
    claim = f["claim_amount"]
    assert claim["value"] == "100000"
    # 金额照旧，出处说明为什么没扣质保金（#143 评审）
    assert "推不出质保期是否届满" in claim["src"] and "待核实" in claim["src"]


def test_non_acceptance_start_explains_reason(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    fields = _case(warranty_period={"value": "24个月", "src": "《虚构合同》质保期自交付使用之日起"})
    claim = apply_derived_fields(fields)["claim_amount"]
    assert claim["value"] == "100000"
    assert "质保期不是自验收合格之日起算" in claim["src"]


def test_no_stages_deducts_undue_retention(monkeypatch):
    # 付款节点拆不出、比例也没定：直接用 未付款 − 未到期质保金
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2099, 6, 1))
    claim = apply_derived_fields(_case(ratio=None))["claim_amount"]
    assert claim["value"] == "50000"
    assert "未付质保金 50000" in claim["src"] and "质保期至 2101年1月10日 才届满" in claim["src"]
