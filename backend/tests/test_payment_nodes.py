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
    raw = _case(clause, 1000000, 700000, 250000, retention_ratio="5%", retention_clause_text="质保金5%，质保期满后支付")
    # 正常图里 retention 在派生字段之前运行：用未派生的原始字段调用，比例已对上节点就不暂停
    assert _retention_asks(raw) == []
    fields = apply_derived_fields(raw)
    assert fields["payable_ratio"]["value"] == "95%"
    assert fields["payment_cite_location"]["value"] == "第七条第4项"
    assert fields["payment_cite_text"]["value"] == "“验收合格后支付合同总价的25%”"  # 小标题「验收款：」不抄


def _retention_asks(fields):
    from app.agent import nodes

    asked = []

    def fake_interrupt(payload):
        asked.append(payload)
        return {}

    orig = nodes.interrupt
    nodes.interrupt = fake_interrupt
    try:
        asyncio.run(nodes.retention_node({"extracted_fields": fields}))
    finally:
        nodes.interrupt = orig
    return asked


def test_unmatched_ratio_with_retention_still_asks_lawyer():
    clause = "1、支付合同总价的70%；2、验收后支付至95%；3、质保金：质保期满后支付合同总价的5%。"
    raw = _case(clause, 1000000, 600000, 250000, retention_ratio="5%", retention_clause_text="质保金5%，质保期满后支付")
    asked = _retention_asks(raw)
    assert asked and asked[0]["pending"]["field_keys"] == ["payable_ratio"]


def test_guarantee_under_retention_heading_is_not_a_payment():
    clause = "1、验收后支付至合同总价的95%；2、质保金：乙方向甲方开具合同总价5%的银行质保保函。"
    assert parse_payment_stages(clause) == []  # 保函不算付款，累计到不了 100%，不拿来匹配
    fields = apply_derived_fields(_case(clause, 1000000, 950000, 50000))
    assert "保函" not in str(fields["payment_cite_text"]["value"])
    # 同一节点里保函之外还有真实付款义务时照常解析
    real = "1、支付合同总价的95%；2、乙方提交合同总价5%的银行保函后15日内甲方支付合同总价的5%。"
    assert [s.end for s in parse_payment_stages(real)] == [95, 100]
    # 余款在前、保函只是付款前提的写法也照常解析
    remainder = "1、验收后甲方向乙方支付至合同总价的95%；2、余款5%在甲方收到乙方银行质保保函后支付。"
    assert [s.end for s in parse_payment_stages(remainder)] == [95, 100]
    assert apply_derived_fields(_case(remainder, 1000000, 700000, 250000))["payable_ratio"]["value"] == "95%"


def test_matched_ratio_keeps_source_risk_in_complaint():
    from app.services.complaint_renderer import render_complaint

    clause = "1、支付合同总价的70%；2、验收后支付至95%；3、质保期满后支付合同总价的5%。"
    for note in ("《合同》第七条（原文未找到，待核实）", "《合同》第七条（OCR识别，待核实）"):
        fields = _case(clause, 1000000, 700000, 250000)
        fields["payment_clause_text"]["src"] = note
        apply_derived_fields(fields)
        assert fields["payable_ratio"]["value"] == "95%"
        assert "待核实" in fields["payable_ratio"]["src"] or "请核实" in fields["payable_ratio"]["src"]
        text = render_complaint(fields, template="支付至{{payable_ratio}}合同款")
        assert text == "支付至⚠️ 待核实：95%合同款"


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


# ── 表格形式的付款条款（律师反馈：付款条款写在表格里，OCR 输出 Markdown / HTML 表格）──────
MD_TABLE = """付款方式如下表：
| 序号 | 付款节点 | 付款比例 | 付款条件 |
|---|---|---|---|
| 1 | 预付款 | 30% | 合同签订后7日内 |
| 2 | 到货款 | 40% | 设备到货验收后15日内 |
| 3 | 验收款 | 25% | 政府验收合格后30日内 |
| 4 | 质保金 | 5% | 质保期满后无息支付 |
"""

HTML_TABLE = (
    "<table><tr><td>期次</td><td>付款条件</td><td>累计付款比例</td></tr>"
    "<tr><td>第1期</td><td>进场后支付</td><td>20%</td></tr>"
    "<tr><td>第2期</td><td>安装完成后支付</td><td>80%</td></tr>"
    "<tr><td>第3期</td><td>验收合格、办理结算后支付</td><td>100%</td></tr></table>"
)


def test_markdown_table_rows_are_stages():
    stages = parse_payment_stages(MD_TABLE)
    assert [(s.no, s.start, s.end) for s in stages] == [("1", 0, 30), ("2", 30, 70), ("3", 70, 95), ("4", 95, 100)]
    assert stages[2].text == "验收款，25%，政府验收合格后30日内"


def test_html_table_with_cumulative_column():
    stages = parse_payment_stages(HTML_TABLE)
    assert [(s.no, s.end) for s in stages] == [("1", 20), ("2", 80), ("3", 100)]


def test_table_with_penalty_row_and_incomplete_table():
    with_penalty = MD_TABLE + "| 5 | 逾期付款违约金 | 0.05% | 每日按欠款计 |\n"
    assert [s.end for s in parse_payment_stages(with_penalty)][-1] == 100
    partial = "| 序号 | 付款比例 |\n|---|---|\n| 1 | 30% |\n| 2 | 40% |\n"
    assert parse_payment_stages(partial) == []  # 只抽到部分行：累计不到 100%，不拿来匹配


def test_table_clause_matches_and_cites_owed_rows():
    fields = apply_derived_fields(_case(MD_TABLE, 1000000, 800000, 150000))  # 已付 80%，付至 95%
    assert fields["payable_ratio"]["value"] == "95%"
    assert fields["payment_cite_location"]["value"] == "第七条第3项"
    assert fields["payment_cite_text"]["value"] == "“验收款，25%，政府验收合格后30日内”"


def test_table_total_row_is_not_a_stage():
    md = MD_TABLE + "| 合计 |  | 100% |  |\n"
    assert [s.end for s in parse_payment_stages(md)] == [30, 70, 95, 100]
    html = HTML_TABLE.replace("</table>", "<tr><td>总计</td><td></td><td>100%</td></tr></table>")
    assert [s.end for s in parse_payment_stages(html)] == [20, 80, 100]


def test_table_guarantee_row_is_not_a_payment():
    table = "| 节点 | 比例 | 条件 |\n|---|---|---|\n| 验收款 | 95% | 验收后支付 |\n| 银行质保保函 | 5% | 乙方向甲方开具保函 |\n"
    assert parse_payment_stages(table) == []  # 保函不算付款，累计到不了 100%，交律师核对
    fields = apply_derived_fields(_case(table, 1000000, 950000, 50000))
    assert "保函" not in str(fields["payment_cite_text"]["value"])
    paid_after = table.replace("乙方向甲方开具保函", "收到乙方保函后支付")
    assert [s.end for s in parse_payment_stages(paid_after)] == [95, 100]


# ── 「第一期：……。第二期：……」写法（#127）──────────────────────────────────────
PERIOD_CLAUSE = (
    "甲方分三期向乙方支付安装费用，具体如下：第一期：甲方在安装前 7 日向乙方支付本批电梯安装总价的 40%作为动工款；"
    "第二期：经验收合格后 15 日内，乙方向甲方移交全部资料后，甲方支付已安装完电梯总价的 55%作为验收款。"
    "该笔款最晚不超过出厂之日起 6 个月。第三期：余款 5%为质保金，质保期满后 30 日内无息付清。"
)


def test_period_markers_split_stages_even_with_full_stops():
    stages = parse_payment_stages(PERIOD_CLAUSE.replace("；", "。"))
    assert [(s.start, s.end) for s in stages] == [(0, 40), (40, 95), (95, 100)]
    assert stages[1].text.startswith("第二期：经验收合格后")


def test_single_period_marker_does_not_split():
    # 只出现一次「第X期」不算分期写法，按原来的分号切
    stages = parse_payment_stages("第一期：签约后支付合同总价的30%；验收后支付至合同总价的100%。")
    assert [(s.start, s.end) for s in stages] == [(0, 30), (30, 100)]


def _period_case():
    fields = _case(PERIOD_CLAUSE, 1000000, 400000, 550000)
    fields["payment_clause_text"]["src"] = "《虚构安装合同.pdf》第4.2条（OCR识别，请核实）"
    fields["payment_clause_location"] = {"value": "第4条", "src": "《虚构安装合同.pdf》第4条付款条件"}
    return fields


def test_period_clause_cites_refined_location_and_owed_period_text():
    fields = apply_derived_fields(_period_case())
    assert fields["payable_ratio"]["value"] == "95%"
    # 位置只写到「第4条」，付款条款原文出自「第4.2条」：引用写到第4.2条（律师写「依据合同第4.2条项下约定」）
    loc = fields["payment_cite_location"]
    assert loc["value"] == "第4.2条"
    assert "第4.2条" in loc["src"] and "OCR" not in loc["value"]
    # 只照抄欠款对应的第二期，「第二期：」小标题不抄
    assert fields["payment_cite_text"]["value"].startswith("“经验收合格后 15 日内")
    assert "质保金" not in fields["payment_cite_text"]["value"]


def test_location_not_refined_when_numbers_disagree():
    # 原文出处是第5.2条、位置是第4条：编号对不上，不替换
    fields = _period_case()
    fields["payment_clause_text"]["src"] = "《虚构安装合同.pdf》第5.2条"
    assert apply_derived_fields(fields)["payment_cite_location"]["value"] == "第4条"
    # 文件名里的数字不算（「《第4.9期工程合同》」）
    fields = _period_case()
    fields["payment_clause_text"]["src"] = "《第4.9条工程合同.pdf》付款条款"
    assert apply_derived_fields(fields)["payment_cite_location"]["value"] == "第4条"
