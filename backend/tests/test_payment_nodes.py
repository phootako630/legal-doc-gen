# 付款节点匹配（确认单（五）第 3 题 + 律师反馈「支付至 95%」）：解析、比例匹配、欠款节点引用。虚构数据。
import asyncio

from app.services.derived_fields import PAYABLE_FROM_STAGES, apply_derived_fields
from app.services.payment_nodes import match_cumulative, owed_stages, parse_payment_stages
from app.services.validators import check_payment_progress

# 定金 5% → 排产 25% → 提货 55% → 验收移交付至 95% → 余款 5%（质保期满后支付）
SALE_CLAUSE = (
    "7.3 甲方自合同签订生效后十五天内向乙方支付本合同总价(除运输费外)的5%作为定金，"
    "此款在每批货款付到85%时直接抵扣货款。"
    "7.4 甲方通知乙方排产时，甲方按排产需求计划支付对应每批总价的25%作为排产款。"
    "7.5 甲方在发货前十五天支付该批次电梯总价的55%作为提货款。"
    "7.6 每批电梯验收合格后，乙方应向甲方移交电梯设备，甲方在收到移交文件后一个月内，"
    "向乙方付至该批次总货款的95%(含前期已支付的定金)。"
    "7.8 余款5%在免费质保期(24个月)满后十五个工作日内甲方支付乙方余下全部货款(无息)。"
)


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


def _case(clause, total, paid, unpaid, **extra):
    fields = _f(total_amount=total, paid_amount=paid, unpaid_amount=unpaid, **extra)
    fields["payment_clause_text"] = {"value": clause, "src": "《合同》第七条"}
    fields["payment_clause_location"] = {"value": "第七条", "src": "《合同》"}
    fields["payment_clause_summary"] = {"value": "AI 归纳的付款条款", "src": "AI 归纳，待核实"}
    return fields


# ── 解析 ──────────────────────────────────────────────────────────────────────
def test_parse_stages_cumulative_and_stage_ratios():
    stages = parse_payment_stages(SALE_CLAUSE)
    assert [(s.no, s.start, s.end) for s in stages] == [
        ("7.3", 0, 5), ("7.4", 5, 30), ("7.5", 30, 85), ("7.6", 85, 95), ("7.8", 95, 100),
    ]  # 「付到85%时」是抵扣条件，不算累计节点


def test_guarantee_percentage_and_day_counts_are_not_payments():
    clause = (
        "4.2.1、预付款：甲方向乙方支付安装总金额的50%；"
        "4.2.2、验收款：验收合格后25个工作日内，甲方向乙方支付安装总金额的40%的验收款；"
        "4.2.3、结算款：办理结算完成后25个工作日内，甲方向乙方支付至安装结算金额的100%，"
        "同时乙方向甲方开具安装结算金额5%的银行质保保函。"
    )
    assert [(s.no, s.end) for s in parse_payment_stages(clause)] == [
        ("4.2.1", 50), ("4.2.2", 90), ("4.2.3", 100),
    ]


def test_items_after_ellipsis_and_semicolon_sub_stages():
    clause = "3.1 本合同所订货物分期付款……3.2 交货前甲方支付本批货物价款的20%。3.3 验收后支付至100%。"
    assert [s.no for s in parse_payment_stages(clause)] == ["3.2", "3.3"]
    items = "1. 进度款：安装进场后，支付合同总价的20%；安装完成后，30个工作日内支付合同总价的60%；\n2. 验收款：验收合格后支付合同总价的20%。"
    stages = parse_payment_stages(items)
    assert [(s.no, s.end) for s in stages] == [("1", 20), ("1", 80), ("2", 100)]
    assert stages[1].text == "安装完成后，30个工作日内支付合同总价的60%"
    fields = apply_derived_fields(_case(items, 1000000, 700000, 300000))
    assert fields["payment_cite_location"]["value"] == "第七条第1项、第2项"


def test_parse_gives_up_when_ratios_exceed_total():
    assert parse_payment_stages("1、支付合同总价的60%；2、支付合同总价的60%。") == []
    assert parse_payment_stages("验收合格后按月支付工程款。") == []
    # 只抽到最后一期：累计到不了 100%，不拿来匹配（免得把原来正确的 100% 变成待补充）
    assert parse_payment_stages("验收合格后支付合同总价的20%。") == []


def test_penalty_percentage_is_not_a_payment_stage():
    clause = "1、验收前支付合同总价的90%；2、结算后支付至100%；甲方逾期支付的，按欠款的0.05%每日支付违约金。"
    assert [s.end for s in parse_payment_stages(clause)] == [90, 100]


def test_owed_stages_span_partial_and_retention():
    stages = parse_payment_stages(SALE_CLAUSE)
    assert match_cumulative(stages, 100).no == "7.8"
    assert [s.no for s in owed_stages(stages, 92.39, 100)] == ["7.6", "7.8"]
    assert match_cumulative(stages, 85.37) is None


# ── 派生字段 ──────────────────────────────────────────────────────────────────
def test_paid_plus_unpaid_matches_last_node_cites_owed_items_verbatim():
    fields = apply_derived_fields(_case(SALE_CLAUSE, "1,000,000.00", "923,900.00", "76,100.00"))
    assert fields["payable_ratio"]["value"] == "100%"
    assert fields["payable_ratio"]["src"].startswith(PAYABLE_FROM_STAGES)
    assert fields["payment_cite_location"]["value"] == "第7.6条、第7.8条"
    text = fields["payment_cite_text"]["value"]
    assert text.startswith("“每批电梯验收合格后") and text.endswith("余下全部货款(无息)”")
    assert "待核实" in fields["payment_cite_text"]["src"]


def test_due_ratio_95_matches_node_and_skips_retention_question():
    # 律师反馈：本次欠款只需付至 95%，原来默认写成 100%
    clause = (
        "1、预付款：支付合同总价的5%；2、排产款：支付合同总价的5%；3、发货款：支付合同总价的60%；"
        "4、验收款：验收合格后支付合同总价的25%；5、质保金：质保期满后支付合同总价的5%。"
    )
    fields = apply_derived_fields(
        _case(clause, 1000000, 700000, 250000, retention_ratio="5%", retention_clause_text="质保金5%，质保期满后支付")
    )
    assert fields["payable_ratio"]["value"] == "95%"
    assert fields["payment_cite_location"]["value"] == "第七条第4项"
    assert fields["payment_cite_text"]["value"] == "“验收合格后支付合同总价的25%”"  # 小标题「验收款：」不抄

    from app.agent.nodes import retention_node

    assert asyncio.run(retention_node({"extracted_fields": fields})) == {}  # 比例已对上节点，不再暂停


def test_unmatched_ratio_left_for_lawyer_with_reason():
    clause = "1、支付合同总价的5%；2、支付合同总价的5%；3、支付合同总价的60%；4、支付合同总价的25%；5、支付合同总价的5%。"
    fields = apply_derived_fields(_case(clause, 1000000, 600000, 250000))
    node = fields["payable_ratio"]
    assert node["value"] is None
    assert "85%" in node["src"] and "手动核对" in node["src"] and "5%、10%、70%、95%、100%" in node["src"]
    # 对不上时不按节点引用，退回 AI 归纳与原条款位置
    assert fields["payment_cite_text"]["value"] == "AI 归纳的付款条款"
    assert fields["payment_cite_location"]["value"] == "第七条"
    check = check_payment_progress(1000000, 600000, 250000, clause)
    assert check.passed and check.needs_review and "手动核对" in check.message


def test_no_parsable_nodes_keeps_previous_behaviour():
    fields = apply_derived_fields(_case("验收合格后付清全部款项。", 1000000, 900000, 100000))
    assert fields["payable_ratio"]["value"] == "100%"
    assert fields["payment_cite_text"]["value"] == "AI 归纳的付款条款"
    assert not check_payment_progress(1000000, 900000, 100000, "验收合格后付清全部款项。").applicable


def test_ocr_payment_clause_keeps_review_mark_on_citation():
    fields = _case(SALE_CLAUSE, 1000000, 923900, 76100)
    fields["payment_clause_text"]["src"] = "《合同》第七条（OCR识别，待核实）"
    apply_derived_fields(fields)
    assert "OCR" in fields["payment_cite_location"]["src"]
    assert "OCR" in fields["payment_cite_text"]["src"]


def test_lawyer_edit_is_kept():
    fields = _case(SALE_CLAUSE, 1000000, 923900, 76100)
    fields["payment_cite_text"] = {"value": "律师改写的付款条款", "src": "律师修改"}
    apply_derived_fields(fields)
    assert fields["payment_cite_text"]["value"] == "律师改写的付款条款"
