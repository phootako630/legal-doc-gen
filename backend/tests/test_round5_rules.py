# 确认单（五）答复落地的规则测试（虚构数据）：原告 / 被告取合同盖章页、应付金额、剩余 / 到期合同款、
# 质保期到期句、补充协议、违约条款称谓、签订地不一致、移交对象等。
from datetime import date

import pytest

from app.config import PLAINTIFF_HQ_NAME
from app.services import derived_fields
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields, finest_clause_no
from app.services.extraction import apply_rule_guards, mark_supplement_agreements
from app.services.file_parser import _identify_type

HQ = PLAINTIFF_HQ_NAME


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


# ── 第 1 题：安装合同原告以合同盖章页乙方为准 ─────────────────────────────────
def test_install_plaintiff_takes_contract_party_b_over_approval_path():
    fields = _f(
        contract_type="安装合同",
        plaintiff_branch_raw="集团/营销网络/江苏分公司/苏州分公司",
        contract_party_b=HQ + "苏州分公司",
    )
    node = apply_derived_fields(fields)["plaintiff_name_final"]
    assert node["value"] == HQ + "苏州分公司"
    assert "仅作参考" in node["src"] and "待核实" not in node["src"]


def test_party_b_that_is_not_plaintiff_is_ignored():
    # 律师反馈：曾把验收报告上的施工单位当成合同乙方
    fields = _f(
        contract_type="安装合同",
        plaintiff_branch_raw="集团/营销网络/江苏分公司",
        contract_party_b="某某智慧楼宇设备有限公司",
    )
    node = apply_derived_fields(fields)["plaintiff_name_final"]
    assert node["value"] == HQ + "江苏分公司"
    assert "不像原告" in node["src"] and "待核实" in node["src"]


def test_party_b_from_ocr_keeps_review_note():
    fields = _f(contract_type="安装合同", plaintiff_branch_raw="集团/江苏分公司")
    fields["contract_party_b"] = {"value": HQ + "江苏分公司", "src": "《合同》盖章页（OCR识别，请核实）"}
    node = apply_derived_fields(fields)["plaintiff_name_final"]
    assert "OCR" in node["src"]


def test_sales_plaintiff_still_head_office():
    fields = _f(contract_type="买卖合同", plaintiff_branch_raw="集团/江苏分公司")
    assert apply_derived_fields(fields)["plaintiff_name_final"]["value"] == HQ


# ── 律师反馈：被告名称以合同盖章页甲方为准 ───────────────────────────────────
def test_defendant_takes_contract_party_a_and_explains_bracket_note():
    fields = _f(defendant_name="某某置业有限公司（某某项目）", contract_party_a="某某置业有限公司")
    node = apply_derived_fields(fields)["defendant_name"]
    assert node["value"] == "某某置业有限公司"
    assert "括号内为备注" in node["src"] and "待核实" not in node["src"]


def test_defendant_mismatch_is_marked_for_review():
    fields = _f(defendant_name="甲置业有限公司", contract_party_a="乙置业有限公司")
    node = apply_derived_fields(fields)["defendant_name"]
    assert node["value"] == "乙置业有限公司" and "待核实" in node["src"]


def test_defendant_keeps_approval_value_without_contract_party_a():
    fields = _f(defendant_name="某某置业有限公司")
    assert apply_derived_fields(fields)["defendant_name"]["value"] == "某某置业有限公司"
    # 先取了合同甲方、后来合同甲方被清掉：退回审批表的值，不留空
    fields = apply_derived_fields(_f(defendant_name="某某置业有限公司（备注）", contract_party_a="某某置业有限公司"))
    fields["contract_party_a"] = {"value": None, "src": ""}
    assert apply_derived_fields(fields)["defendant_name"]["value"] == "某某置业有限公司（备注）"


def test_party_from_acceptance_report_is_dropped():
    fields = {
        "contract_party_b": {"value": "某施工公司", "src": "《验收报告.pdf》施工单位"},
        "contract_party_a": {"value": "某使用单位", "src": "《电梯监督检验报告》使用单位名称"},
    }
    apply_rule_guards(fields)
    assert fields["contract_party_b"]["value"] is None
    assert fields["contract_party_a"]["value"] is None


# ── 第 5 题：写出应付金额；第 9 题：剩余 / 到期合同款 ──────────────────────────
def test_payable_amount_is_paid_plus_unpaid():
    fields = apply_derived_fields(
        _f(total_amount="1,000,000.00", paid_amount="900000", unpaid_amount="100000")
    )
    assert fields["payable_amount"]["value"] == "1000000"
    assert fields["debt_term"]["value"] == "剩余合同款"


@pytest.mark.parametrize(
    ("paid", "unpaid", "term", "review"),
    [
        (900000, 50000, "到期合同款", False),  # 尚有未到期部分
        (900000, 150000, "到期合同款", True),  # 大于总价：可能调过价，交律师
    ],
)
def test_debt_term(paid, unpaid, term, review):
    node = apply_derived_fields(_f(total_amount=1000000, paid_amount=paid, unpaid_amount=unpaid))[
        "debt_term"
    ]
    assert node["value"] == term
    assert ("待核实" in node["src"]) is review


def test_debt_term_in_claim_and_payable_amount_in_facts():
    fields = apply_derived_fields(_f(total_amount=1000000, paid_amount=900000, unpaid_amount=50000))
    text = render_complaint(
        fields,
        template="1、判令被告向原告支付{{debt_term}}¥{{unpaid_amount}}元；支付至100%合同款¥{{payable_amount}}元",
    )
    assert text.startswith("1、判令被告向原告支付到期合同款¥")
    # 支付至 100% 但 已付 + 欠款 ≠ 总价：金额照写，标待核实交律师
    assert "100%合同款¥⚠️ 待核实：950000元" in text


# ── 第 7 题：欠款含质保金时写质保期到期句 ─────────────────────────────────────
def _retention_fields(**extra):
    return _f(
        retention_unpaid_amount="164,630.70元",
        acceptance_latest_date="2022年12月26日",
        warranty_period="24个月",
        **extra,
    )


def test_warranty_expiry_sentence(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2026, 1, 1))
    node = apply_derived_fields(_retention_fields())["warranty_expiry_sentence"]
    assert node["value"] == "，质保期已于2024年12月26日到期"
    assert "待核实" in node["src"]


def test_warranty_not_yet_expired_writes_nothing(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2024, 6, 1))
    node = apply_derived_fields(_retention_fields())["warranty_expiry_sentence"]
    assert node["value"] == "" and "才届满" in node["src"]


def test_no_retention_in_debt_writes_nothing():
    fields = apply_derived_fields(_retention_fields(**{}) | _f(retention_unpaid_amount="0.00元"))
    assert fields["warranty_expiry_sentence"]["value"] == ""


def test_warranty_not_from_acceptance_is_left_to_lawyer(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2026, 1, 1))
    fields = _retention_fields()
    fields["warranty_period"] = {"value": "24个月", "src": "《合同》第7.8条（自交付使用之日起计算）"}
    node = apply_derived_fields(fields)["warranty_expiry_sentence"]
    assert node["value"] == "" and "不是自验收" in node["src"]


# ── 第 8 题：补充协议 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name",
    ["设备补协.pdf", "某项目电梯设备买卖合同补充协议.pdf", "合同修改单.pdf", "变更协议.docx"],
)
def test_supplement_files_identified_before_contract(name):
    assert _identify_type(name) == "补充协议"


def test_litigation_flow_file_is_approval_form():
    assert _identify_type("某某置业有限公司诉讼流程文件.pdf") == "审批表"


def test_supplement_sentence_and_original_price():
    fields = _f(total_amount="3,466,458.00")
    fields["contract_total_in_contract"] = {"value": "3772458", "src": "《设备合同.pdf》第2条"}
    mark_supplement_agreements(fields, [{"filename": "设备补协.pdf", "identified_type": "补充协议"}])
    apply_derived_fields(fields)
    assert fields["contract_total_stated"]["value"] == "3772458"
    assert fields["supplement_sentence"]["value"] == "后双方签订补充协议，确认合同总价变更为¥3466458元。"
    assert "待核实" in fields["supplement_sentence"]["src"]
    text = render_complaint(
        fields, template="合同总价为¥{{contract_total_stated}}元。{{supplement_sentence}}双方对"
    )
    assert "合同总价为¥⚠️ 待核实：3772458元。⚠️ 待核实：后双方签订补充协议" in text


def test_without_supplement_total_is_approval_total_and_no_sentence():
    fields = apply_derived_fields(_f(total_amount="841,534.00"))
    assert fields["contract_total_stated"]["value"] == "841,534.00"
    assert fields["supplement_sentence"]["value"] == ""
    assert render_complaint(fields, template="{{supplement_sentence}}双方对") == "双方对"


# ── 第 13 题：违约条款引用 ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        ("第十条第10.2条", "第10.2条"),
        ("第10.1条", "第10.1条"),
        ("第十一条第4款", "第十一条第4款"),  # 没有分级编号，不猜
        ("第7.6条及第7.8条", "第7.6条及第7.8条"),  # 多条不合并
    ],
)
def test_finest_clause_no(loc, expected):
    assert finest_clause_no(loc) == expected


def test_breach_sentence_uses_litigant_terms():
    fields = apply_derived_fields(
        _f(
            breach_interest_clause_location="第十条第10.2条",
            breach_clause_text="甲方未按时支付安装费的，按未付款每天万分之二向乙方支付违约金。",
        )
    )
    assert fields["breach_clause_sentence"]["value"] == (
        "依据合同第10.2条约定，被告未按时支付安装费的，按未付款每天万分之二向原告支付违约金。"
    )


# ── 第 15 题：封面与盖章页签订地不一致交律师 ─────────────────────────────────
_SIGN_DISPUTE = "因本合同发生的纠纷，向合同签订地人民法院起诉。"


def test_sign_place_conflict_left_to_lawyer():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_SIGN_DISPUTE,
            contract_sign_place="河北省保定市莲池区某路1号",
            contract_sign_place_cover="江苏省南京市鼓楼区某路2号",
        )
    )
    assert fields["court_district"]["value"] is None
    assert "不一致" in fields["court_district"]["src"]


def test_sign_place_same_or_cover_only_still_inferred():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_SIGN_DISPUTE,
            contract_sign_place="河北省保定市莲池区某路1号",
            contract_sign_place_cover="河北省保定市莲池区某路1号（签约处）",
        )
    )
    assert fields["court_district"]["value"] == "保定市莲池区"
    fields = apply_derived_fields(
        _f(dispute_clause_text=_SIGN_DISPUTE, contract_sign_place_cover="河北省保定市莲池区某路1号")
    )
    assert fields["court_district"]["value"] == "保定市莲池区"


def test_warranty_from_certificate_and_handover_still_computed(monkeypatch):
    # 「自获得检验合格证并正式移交甲方之日起算」：律师认可按最晚验收日推算并标黄核实
    monkeypatch.setattr(derived_fields, "_today", lambda: date(2026, 1, 1))
    fields = _retention_fields()
    fields["warranty_period"] = {
        "value": "24个月",
        "src": "《合同》第9.4款（自获得《电梯检验合格证》并正式移交甲方之日起算）",
    }
    node = apply_derived_fields(fields)["warranty_expiry_sentence"]
    assert node["value"] == "，质保期已于2024年12月26日到期"
    assert "按验收日推算" in node["src"]


def test_retention_return_interest_is_not_payment_breach_clause():
    fields = {
        "breach_clause_text": {
            "value": "甲方无约定或法定理由未按时返还保修款的，乙方有权要求甲方支付逾期付款期间的存款利息。",
            "src": "《合同》附件1第4.3条",
        },
        "breach_interest_rate_text": {"value": "按银行同期存款利率计算的存款利息", "src": "《合同》"},
        "breach_interest_clause_location": {"value": "附件1第4.3条", "src": "《合同》"},
    }
    apply_rule_guards(fields)
    assert fields["breach_interest_rate_text"]["value"] is None
    assert apply_derived_fields(fields)["interest_term"]["value"] == "逾期付款利息"


def test_payment_breach_clause_mentioning_retention_is_kept():
    fields = {
        "breach_clause_text": {
            "value": "甲方逾期支付工程款或逾期返还质保金的，每日按万分之五支付违约金。",
            "src": "《合同》第10条",
        }
    }
    apply_rule_guards(fields)
    assert fields["breach_clause_text"]["value"]


@pytest.mark.parametrize("word", ["逾期", "延期", "迟延"])
def test_daily_rate_with_various_delay_words_is_periodic(word):
    # 「每延期一日按……万分之二」是按日计付的违约金，不是一次性金额（湖南庆合买卖合同第 10.1 条）
    fields = apply_derived_fields(
        _f(
            breach_clause_text=f"甲方没有按期支付合同价款的，每{word}一日按应付而未付款项的万分之二向乙方支付违约金。",
            breach_interest_rate_text="每日万分之二的标准",
            unpaid_amount=263892.1,
        )
    )
    assert fields["interest_rate_basis"]["value"] == "每日万分之二的标准"
    assert not fields.get("penalty_amount") or fields["penalty_amount"]["value"] in (None, "")
