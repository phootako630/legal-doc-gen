# 违约条款只留甲方逾期付款那一句（#124）：乙方延迟交货的「违约金」不能让系统把逾期付款利息认成违约金。
# 虚构条款。
from app.services.derived_fields import LPR_INTEREST_BASIS, interest_basis_field, interest_term_field
from app.services.extraction import SELLER_RATE_DROPPED_NOTE, apply_rule_guards

_SRC = "《虚构买卖合同.pdf》第三条（OCR识别，请核实）"


def _guard(text, rate=None):
    fields = {"breach_clause_text": {"value": text, "src": _SRC}}
    if rate is not None:
        fields["breach_interest_rate_text"] = {"value": rate, "src": _SRC}
    apply_rule_guards(fields)
    return fields


def test_seller_delay_penalty_dropped_buyer_interest_kept():
    text = ("每批次延迟交货一天，乙方向甲方支付的违约金金额为：延迟交货批次设备总价万分之五；"
            "甲方未按合同规定期限支付货款的，应就拖欠金额按中国人民银行同期贷款利率向乙方支付利息。")
    fields = _guard(text)
    node = fields["breach_clause_text"]
    assert node["value"] == "甲方未按合同规定期限支付货款的，应就拖欠金额按中国人民银行同期贷款利率向乙方支付利息。"
    assert node["raw_value"] == text
    assert "乙方延迟交货" in node["src"]
    assert interest_term_field(fields)["value"] == "逾期付款利息"


def test_buyer_penalty_kept_with_continuation():
    text = ("除不可抗力原因外，甲方未能按本合同规定支付款项，甲方应向乙方支付逾期违约金，每延误一周为延误部分的万分之二。"
            "此违约金最高不得超过延误部分合同金额的万分之二。")
    fields = _guard(text)
    assert fields["breach_clause_text"] == {"value": text, "src": _SRC}
    assert interest_term_field(fields)["value"] == "违约金"


def test_both_penalties_keep_only_buyer_sentence():
    text = ("乙方逾期交货的，每逾期一日按合同总价的万分之三支付违约金。"
            "买方逾期付款的，每逾期一日按应付未付款的万分之五向卖方支付违约金。")
    fields = _guard(text)
    assert fields["breach_clause_text"]["value"] == "买方逾期付款的，每逾期一日按应付未付款的万分之五向卖方支付违约金。"
    assert interest_term_field(fields)["value"] == "违约金"


def test_unrecognized_clause_untouched():
    # 认不出谁逾期付款的条款不动（不猜）
    text = "任何一方违约，应向守约方支付合同总价 5% 的违约金。"
    assert _guard(text)["breach_clause_text"] == {"value": text, "src": _SRC}


def test_guard_is_idempotent():
    text = "乙方延迟交货的，按日万分之五支付违约金；甲方延期付款的，按同期贷款利率支付利息。"
    fields = _guard(text)
    once = dict(fields["breach_clause_text"])
    apply_rule_guards(fields)
    assert fields["breach_clause_text"] == once


def test_reverse_order_and_unpunctual_wording():
    # 甲方在前、乙方「未按期交货」在后，也要认出乙方的句子（#130 评审）
    text = "甲方未按期付款的，应按同期贷款利率支付利息；乙方未按期交货的，应支付合同总价5%的违约金。"
    fields = _guard(text)
    assert fields["breach_clause_text"]["value"] == "甲方未按期付款的，应按同期贷款利率支付利息；"
    assert interest_term_field(fields)["value"] == "逾期付款利息"
    for seller in ("乙方未能按时交付的", "卖方未如期供货的", "乙方不能按期完工的"):
        text = f"甲方逾期付款的，按同期贷款利率支付利息；{seller}，应支付违约金。"
        assert _guard(text)["breach_clause_text"]["value"] == "甲方逾期付款的，按同期贷款利率支付利息；"


def test_rate_from_dropped_seller_sentence_cleared():
    # 违约利率抽成了乙方的「违约金」：只在裁掉的乙方句子里出现，一并去掉，否则仍被认成违约金（#130 评审）
    text = "乙方延迟交货的，每日按合同总价万分之五支付违约金；甲方延期付款的，应向乙方支付逾期付款利息。"
    fields = _guard(text, rate="每日按合同总价万分之五支付违约金")
    rate = fields["breach_interest_rate_text"]
    assert rate["value"] is None
    assert rate["raw_value"] == "每日按合同总价万分之五支付违约金"
    assert SELLER_RATE_DROPPED_NOTE in rate["src"]
    assert interest_term_field(fields)["value"] == "逾期付款利息"
    # 甲方句子没写利率：按常规 LPR
    assert interest_basis_field(fields)["value"] == LPR_INTEREST_BASIS
    # 再跑一次不变
    once = {k: dict(v) for k, v in fields.items()}
    apply_rule_guards(fields)
    assert fields == once


def test_rate_cleared_but_buyer_sentence_has_rate_no_lpr():
    # 甲方句子另写了利率、抽出的却是乙方的：不套用 LPR，交律师按原文填
    text = "乙方延迟交货的，每日按合同总价万分之五支付违约金；甲方延期付款的，按每日万分之三支付逾期付款利息。"
    fields = _guard(text, rate="每日按合同总价万分之五支付违约金")
    assert fields["breach_interest_rate_text"]["value"] is None
    basis = interest_basis_field(fields)
    assert basis["value"] is None and "LPR" in basis["src"]


def test_rate_in_kept_buyer_sentence_untouched():
    text = "乙方延迟交货的，按日万分之五支付违约金；甲方延期付款的，按每日万分之三支付利息。"
    fields = _guard(text, rate="每日万分之三")
    assert fields["breach_interest_rate_text"] == {"value": "每日万分之三", "src": _SRC}
