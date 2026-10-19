# derived_fields 单测：逾期利息常规话术 + 按工程所在地推定管辖法院 + 律师修改不被覆盖
import pytest

from app.services.derived_fields import (
    LPR_INTEREST_BASIS,
    apply_derived_fields,
    derive_court_district,
)


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("江苏省南京市雨花台区某某二期项目", "南京市雨花台区"),
        ("南京市鼓楼区某路1号", "南京市鼓楼区"),
        ("上海市浦东新区张江路1号", "上海市浦东新区"),
        ("广西壮族自治区南宁市青秀区民族大道", "南宁市青秀区"),
        ("江苏省苏州市昆山市花桥镇", "昆山市"),
        ("江苏省徐州市沛县某镇某路", "沛县"),  # 县只写县名（补充答复 Q5）  # 县级市只写县级市（确认单四第 3 题）
        ("山东省济南市市中区经十路", "济南市市中区"),
        ("位于南京市雨花台区", "南京市雨花台区"),
        # 合同省了「市」字：两字城市名 + 市辖区
        ("苏州吴江区某某路以北、某某路以东地块", "苏州市吴江区"),
    ],
)
def test_derive_court_district(site, expected):
    assert derive_court_district(site) == expected


@pytest.mark.parametrize(
    "site",
    [
        None,
        "",
        "某某二期项目",
        "雨花台区某某二期",
        "某某大厦附近南京市",
        "鼓楼区某路1号",
        # 开发区 / 园区等功能区未必设法院，不推
        "苏州工业园区某某路",
        "南京江北新区某某路",
        # 三四字城市 / 三字区名省了「市」字：分不清城市边界，不推（#43 复审）
        "乌鲁木齐天山区某路",
        "呼和浩特赛罕区某路",
        "石家庄长安区某路",
        "南京雨花台区某某二期",
    ],
)
def test_derive_court_district_unknown_returns_none(site):
    # 推不出宁可不推（宁缺勿造），交律师填
    assert derive_court_district(site) is None


def test_interest_basis_defaults_to_lpr():
    fields = apply_derived_fields({})
    node = fields["interest_rate_basis"]
    assert node["value"] == LPR_INTEREST_BASIS
    assert "待核实" not in node["src"]


def test_interest_basis_uses_contract_rate():
    # 律师规则：合同约定了甲方逾期付款利率就用约定（标待核实）
    fields = apply_derived_fields(
        {
            "breach_interest_rate_text": {
                "value": "每日万分之五的标准",
                "src": "《合同》",
            }
        }
    )
    node = fields["interest_rate_basis"]
    assert node["value"] == "每日万分之五的标准"
    assert "待核实" in node["src"]


def test_court_district_marked_uncertain():
    fields = apply_derived_fields(
        {"project_site": {"value": "江苏省南京市雨花台区某某二期项目", "src": "x"}}
    )
    node = fields["court_district"]
    assert node["value"] == "南京市雨花台区"
    assert "待核实" in node["src"]


def test_court_district_skipped_when_arbitration():
    fields = apply_derived_fields(
        {
            "project_site": {"value": "江苏省南京市雨花台区", "src": "x"},
            "dispute_clause_text": {"value": "提交南京仲裁委员会仲裁", "src": "x"},
        }
    )
    assert fields["court_district"]["value"] is None
    assert "仲裁" in fields["court_district"]["src"]


def test_rederives_when_dependency_changes():
    fields = apply_derived_fields(
        {"project_site": {"value": "江苏省南京市雨花台区", "src": "x"}}
    )
    fields["project_site"] = {"value": "江苏省南京市鼓楼区", "src": "律师确认修改"}
    apply_derived_fields(fields)
    assert fields["court_district"]["value"] == "南京市鼓楼区"


def test_lawyer_edit_not_overwritten():
    fields = apply_derived_fields(
        {"project_site": {"value": "江苏省南京市雨花台区", "src": "x"}}
    )
    # 审核页编辑：值改写、src 改为「律师人工确认修改」（derived 标记仍在）
    fields["court_district"]["value"] = "南京市中级"
    fields["court_district"]["src"] = "律师人工确认修改"
    fields["interest_rate_basis"] = {
        "value": "每日万分之五的标准",
        "src": "律师确认修改",
    }
    apply_derived_fields(fields)
    assert fields["court_district"]["value"] == "南京市中级"
    assert fields["interest_rate_basis"]["value"] == "每日万分之五的标准"


# ── 律师确认单规则 ────────────────────────────────────────────────────────────
from app.services.derived_fields import derive_plaintiff_name  # noqa: E402

HQ = "日立电梯（中国）有限公司"


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("安装", "集团/营销网络/江苏分公司", (HQ + "江苏分公司", False)),
        ("安装", "集团/营销网\n络/江苏分公司", (HQ + "江苏分公司", False)),  # 表格折行
        ("安装", "集团/营销网络/江苏分公司/南京分公司", (HQ + "江苏分公司", True)),
        ("买卖", "集团/营销网络/江苏分公司", (HQ, False)),
        ("安装", "集团/营销网络", None),
        ("安装", None, None),
    ],
)
def test_derive_plaintiff_name(kind, raw, expected):
    assert derive_plaintiff_name(kind, raw) == expected


def test_plaintiff_name_overrides_llm_but_not_lawyer():
    fields = _f(
        contract_type="安装合同", plaintiff_branch_raw="集团/营销网络/江苏分公司"
    )
    fields["plaintiff_name_final"] = {"value": "江苏分公司", "src": "《审批表》"}
    apply_derived_fields(fields)
    assert fields["plaintiff_name_final"]["value"] == HQ + "江苏分公司"
    fields["plaintiff_name_final"] = {
        "value": "律师改的名称",
        "src": "律师人工确认修改",
    }
    apply_derived_fields(fields)
    assert fields["plaintiff_name_final"]["value"] == "律师改的名称"


def test_plaintiff_phone_fixed():
    fields = apply_derived_fields({})
    assert fields["plaintiff_phone"]["value"] == "0755-83679974"


def test_sale_contract_wording():
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    assert fields["contract_action"]["value"] == "供货"
    assert fields["price_term"]["value"] == "产品价格"
    assert fields["plaintiff_name_final"]["value"] == HQ
    fields = apply_derived_fields(_f(contract_type="安装合同"))
    assert fields["contract_action"]["value"] == "安装"
    assert fields["price_term"]["value"] == "安装价格"


def test_branch_registry_fills_plaintiff(tmp_path, monkeypatch):
    import json

    from app.services import branch_registry

    path = tmp_path / "branch_info.json"
    path.write_text(
        json.dumps(
            {
                "headquarters": {
                    "name": HQ,
                    "credit_code": "HQCODE",
                    "address": "总部地址",
                },
                "branches": [
                    {
                        "name": "江苏分公司",
                        "credit_code": "JSCODE",
                        "person_in_charge": "王某，总经理",
                        "address": "南京某地址",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(branch_registry, "BRANCH_INFO_PATH", str(path))
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="集团/营销网络/江苏分公司")
    )
    assert fields["plaintiff_credit_code"]["value"] == "JSCODE"
    assert fields["plaintiff_person_in_charge"]["value"] == "王某，总经理"
    assert fields["plaintiff_address"]["value"] == "南京某地址"
    assert fields["plaintiff_credit_code"]["src"] == "《原告信息表》"  # 早期格式没有更新日期


def test_branch_registry_missing_file_leaves_blank():
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="集团/营销网络/江苏分公司")
    )
    assert "plaintiff_credit_code" not in fields


@pytest.mark.parametrize(
    ("approval", "contract", "acceptance", "expected", "uncertain"),
    [
        (15, 14, 14, 14, False),  # 本案：审批表填错，以验收报告为准
        (14, 14, 13, 13, True),  # 审批表与合同一致、报告不同 → 待核实
        (14, 14, 14, 14, False),
    ],
)
def test_elevator_qty_follows_acceptance(
    approval, contract, acceptance, expected, uncertain
):
    fields = apply_derived_fields(
        _f(
            elevator_qty=approval,
            elevator_qty_by_approval=approval,
            elevator_qty_by_contract=contract,
            elevator_qty_by_acceptance=acceptance,
        )
    )
    node = fields["elevator_qty"]
    assert node["value"] == expected
    assert ("待核实" in node["src"]) is uncertain


def test_elevator_qty_kept_without_acceptance():
    fields = apply_derived_fields(_f(elevator_qty=15, elevator_qty_by_approval=15))
    assert fields["elevator_qty"]["value"] == 15
    assert "derived" not in fields["elevator_qty"]


@pytest.mark.parametrize(
    ("dispute", "extra", "text_part", "court"),
    [
        (
            "双方向工程所在地的当地法院提起诉讼",
            {"project_site": "江苏省南京市雨花台区某某二期"},
            "因工程所在地为江苏省南京市雨花台区某某二期，属南京市雨花台区法院辖区",
            "南京市雨花台区",
        ),
        (
            "向原告所在地人民法院起诉",
            {"plaintiff_address": "南京市鼓楼区某路1号"},
            "故原告向南京市鼓楼区人民法院提起诉讼。",
            "南京市鼓楼区",
        ),
        (
            "由甲方所在地人民法院管辖",
            {"defendant_address": "南京市秦淮区某路2号"},
            "故原告向南京市秦淮区人民法院提起诉讼。",
            "南京市秦淮区",
        ),
        (
            "双方可向人民法院提起诉讼",
            {"defendant_address": "南京市秦淮区某路2号"},
            "依据《中华人民共和国民事诉讼法》第二十四条，故原告向南京市秦淮区人民法院提起诉讼。",
            "南京市秦淮区",
        ),
        ("提交南京仲裁委员会仲裁", {}, "故申请人向南京仲裁委员会提请仲裁。", None),
        ("由南京市中级人民法院管辖", {}, "故原告向【待补充】人民法院提起诉讼。", None),
    ],
)
def test_jurisdiction_modes(dispute, extra, text_part, court):
    fields = apply_derived_fields(_f(dispute_clause_text=dispute, **extra))
    assert fields["court_district"]["value"] == court
    text = fields["jurisdiction_text"]["value"]
    if text_part is None:
        assert text is None
    else:
        assert text_part in text
        assert "待核实" in fields["jurisdiction_text"]["src"]


@pytest.mark.parametrize(
    ("ratio", "expected", "uncertain"),
    [
        ("无", "100%", False),
        ("5%", "100%", True),  # 有质保金：等律师在断点选定
        (None, "100%", True),
    ],
)
def test_payable_ratio_default(ratio, expected, uncertain):
    fields = _f(retention_ratio=ratio)
    apply_derived_fields(fields)
    node = fields["payable_ratio"]
    assert node["value"] == expected
    assert ("待核实" in node["src"]) is uncertain


@pytest.mark.parametrize(
    ("ratio", "clause", "options"),
    [
        ("5%", "质保金为合同总价的5%", ["100%", "95%"]),
        ("5%", "质保期满一年支付2%，满二年支付3%", ["100%", "97%", "95%"]),
        ("5%", None, ["100%", "95%"]),
        ("5%", "质保期满一年支付2%", ["100%", "95%"]),  # 分期对不上总比例 → 按一期
    ],
)
def test_payable_ratio_options(ratio, clause, options):
    from app.services.derived_fields import payable_ratio_options

    assert (
        payable_ratio_options(_f(retention_ratio=ratio, retention_clause_text=clause))
        == options
    )


def test_lpr_wording_one_year():
    fields = apply_derived_fields({})
    assert (
        fields["interest_rate_basis"]["value"]
        == "全国银行间同业拆借中心公布的一年期贷款市场报价利率"
    )


@pytest.mark.parametrize(
    ("clause", "handover", "expected"),
    [
        # 确认单（五）第 6 题：合同约定移交物业才写物业，否则移交被告
        ("验收合格、完整移交并办理完工程结算手续后支付", None, "已全部移交被告并办理结算"),
        ("验收合格后30日内支付至100%", None, "已全部移交被告"),
        (None, None, "已全部移交被告"),
        ("验收合格后30日内支付至100%", "验收合格后乙方将电梯移交甲方指定的物业单位", "已全部移交物业"),
        ("验收合格并移交物业公司后支付95%，结算后付清", None, "已全部移交物业并办理结算"),
    ],
)
def test_handover_text(clause, handover, expected):
    fields = apply_derived_fields(_f(payment_clause_text=clause, handover_clause_text=handover))
    assert fields["handover_text"]["value"] == expected


def test_plaintiff_follows_contract_party_b_when_different():
    fields = _f(
        contract_type="安装合同",
        plaintiff_branch_raw="集团/营销网络/湖南分公司/郴州分公司",
        contract_party_b="日立电梯（中国）有限公司湖南分公司",
    )
    apply_derived_fields(fields)
    node = fields["plaintiff_name_final"]
    assert node["value"] == HQ + "湖南分公司"
    assert "以合同盖章页乙方为准" in node["src"]

    fields = _f(
        contract_type="安装合同",
        plaintiff_branch_raw="集团/营销网络/湖南分公司",
        contract_party_b="日立电梯（中国）有限公司郴州分公司",
    )
    apply_derived_fields(fields)
    node = fields["plaintiff_name_final"]
    # 确认单（五）第 1 题：安装合同一律以合同乙方为准，审批表只作参考，不再标待核实
    assert node["value"] == HQ + "郴州分公司"
    assert "以合同盖章页乙方为准" in node["src"] and "仅作参考" in node["src"]
    assert "待核实" not in node["src"]


def test_plaintiff_rep_label():
    assert (
        apply_derived_fields(_f(contract_type="买卖合同"))["plaintiff_rep_label"][
            "value"
        ]
        == "法定代表人"
    )
    assert (
        apply_derived_fields(_f(contract_type="安装合同"))["plaintiff_rep_label"][
            "value"
        ]
        == "负责人"
    )


def test_qty_slots_contract_and_acceptance():
    fields = apply_derived_fields(
        _f(
            elevator_qty_by_approval=15,
            elevator_qty_by_contract=15,
            elevator_qty_by_acceptance=14,
            elevator_qty_vge=1,
        )
    )
    assert fields["elevator_qty_contract"]["value"] == 15
    assert fields["elevator_qty"]["value"] == 14
    assert "家用电梯" in fields["elevator_qty"]["src"]
    # 合同没识别到：约定台数暂取审批表并标待核实
    fields = apply_derived_fields(
        _f(elevator_qty_by_approval=15, elevator_qty_by_acceptance=15)
    )
    assert fields["elevator_qty_contract"]["value"] == 15
    assert "待核实" in fields["elevator_qty_contract"]["src"]


def test_document_kind_and_addressee():
    fields = apply_derived_fields(_f(dispute_clause_text="提交南京仲裁委员会仲裁"))
    assert fields["document_kind"]["value"] == "仲裁申请书"
    assert fields["addressee"]["value"] == "南京仲裁委员会"
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地法院起诉",
            project_site="江苏省南京市雨花台区某项目",
        )
    )
    assert fields["document_kind"]["value"] == "民事起诉状"
    assert fields["addressee"]["value"] == "南京市雨花台区人民法院"


# 合同里的「5%」是乙方另开的银行质保保函，不是从合同款扣留的质保金（新案卷核对）
_GUARANTEE_CLAUSE = (
    "办理结算完成后25个工作日内，甲方向乙方支付至安装结算金额的100%，同时乙方向甲方开具"
    "安装结算金额5%，有效期至质保期（两年）满的银行质保保函作为对乙方保修期内安装服务的担保"
)


@pytest.mark.parametrize(
    ("clause", "guarantee"),
    [
        (_GUARANTEE_CLAUSE, True),
        ("预留合同金额5%的质保金，质保期满后无息返还", False),
        # 否定保函 / 保函只是付款前提：合同款里仍有质保金，要问律师（#43 复审）
        ("本合同不采用银行保函，质保金为合同价款5%，质保期满后支付", False),
        ("剩余5%质保金待乙方提交银行保函后支付", False),
        # 只出现「保函」二字、没写乙方开具：拿不准，按质保金处理
        ("质保期内以银行保函担保", False),
        # 既扣留质保金、又可用保函替换：仍按质保金问律师
        ("预留5%质保金，乙方可提供银行保函替换，质保期满后返还", False),
        (None, False),
    ],
)
def test_retention_is_guarantee(clause, guarantee):
    from app.services.derived_fields import retention_is_guarantee

    fields = _f(retention_ratio="5%", retention_clause_text=clause)
    assert retention_is_guarantee(fields) is guarantee


def test_guarantee_only_pays_to_full_contract():
    fields = apply_derived_fields(
        _f(retention_ratio="5%", retention_clause_text=_GUARANTEE_CLAUSE)
    )
    node = fields["payable_ratio"]
    assert node["value"] == "100%"
    assert "保函" in node["src"] and "待核实" in node["src"]


def test_retention_node_skips_guarantee_only():
    import asyncio

    from app.agent.nodes import retention_node

    state = {"extracted_fields": _f(retention_ratio="5%", retention_clause_text=_GUARANTEE_CLAUSE)}
    # 不进 interrupt（在图外调用 interrupt 会抛错）：直接返回、不改字段
    assert asyncio.run(retention_node(state)) == {}


# 约定被告所在地法院、工商登记住址还没查：按审批表买方地址推断法院并在管辖句写明（新案卷核对）
def test_defendant_seat_court_from_reference_address_real_shape():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="协商不成，任何一方可依法向甲方所在地的人民法院起诉",
            defendant_address_ref="湖南省邵阳市邵东市某某街道某路1号",
        )
    )
    assert fields["court_district"]["value"] == "邵东市"
    assert "待核实" in fields["court_district"]["src"]
    text = fields["jurisdiction_text"]["value"]
    assert "【系统按被告地址「湖南省邵阳市邵东市某某街道某路1号」推断，待核实】" in text
    assert text.endswith("故原告向邵东市人民法院提起诉讼。")


def test_defendant_seat_court_prefers_registered_address():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向甲方所在地的人民法院起诉",
            defendant_address="江苏省南京市鼓楼区某路1号",
            defendant_address_ref="湖南省邵阳市邵东市某某街道某路1号",
        )
    )
    assert fields["court_district"]["value"] == "南京市鼓楼区"
    assert "推断" not in fields["jurisdiction_text"]["value"]


@pytest.mark.parametrize(
    ("clause", "rate", "cap"),
    [
        (
            "甲方未按规定的时间和金额支付安装费的，按未付款金额每天万分之二的比例向乙方支付"
            "逾期付款违约金。甲方逾期付款违约金最高不超过安装费总额的 10%。",
            "每日万分之二的标准",
            "，最高不超过安装费总额的10%",
        ),
        ("甲方逾期付款的，按每日万分之五支付违约金", "每日万分之五的标准", ""),
    ],
)
def test_penalty_cap(clause, rate, cap):
    fields = apply_derived_fields(
        _f(breach_clause_text=clause, breach_interest_rate_text=rate)
    )
    assert fields["penalty_cap"]["value"] == cap


def test_penalty_cap_rendered_in_claim():
    from app.services.complaint_renderer import render_complaint

    clause = "甲方逾期付款违约金按每天万分之二计，最高不超过安装费总额的10%"
    text = render_complaint(
        _f(breach_clause_text=clause, breach_interest_rate_text="每日万分之二的标准", unpaid_amount="1000")
    )
    # 上限照合同写在括号末尾（来源待核实，前面带核实标记）
    assert "计至实际付清之日止⚠️ 待核实：，最高不超过安装费总额的10%）" in text
    # 无上限时括号照旧闭合，不留占位符或【待补充】
    plain = render_complaint(_f(unpaid_amount="1000"))
    assert "计至实际付清之日止）" in plain and "penalty_cap" not in plain


@pytest.mark.parametrize(
    ("clause", "cap"),
    [
        # 金额千分位逗号不截断（半角 / 全角）
        ("甲方逾期付款的，按每日万分之五支付违约金，最高不超过人民币10,000元。", "，最高不超过人民币10,000元"),
        ("甲方逾期付款的，按每日万分之五支付违约金，最高不超过人民币10，000元，另行", "，最高不超过人民币10，000元"),
        # 普通逗号仍断句
        ("按每日万分之五支付违约金，最高不超过合同总价的5%，并赔偿损失", "，最高不超过合同总价的5%"),
        # 分不清上限写到哪：不输出截断金额，留【待补充】
        ("按每日万分之五支付违约金，最高不超过1,5", None),
    ],
)
def test_penalty_cap_thousands_separator(clause, cap):
    fields = apply_derived_fields(
        _f(breach_clause_text=clause, breach_interest_rate_text="每日万分之五的标准")
    )
    assert fields["penalty_cap"]["value"] == cap


def test_elevator_qty_keeps_ocr_warning_from_scanned_reports():
    from app.services.complaint_renderer import render_complaint

    fields = _f(elevator_qty_by_approval="2", elevator_qty_by_contract="2")
    fields["elevator_qty_by_acceptance"] = {
        "value": 2,
        "src": "《验收报告.pdf》等：扫描报告按首页计数 2 份，共 2 台（OCR识别，请核实）",
        "channel": "ocr",
    }
    out = apply_derived_fields(fields)
    assert out["elevator_qty"]["channel"] == "ocr"
    assert "OCR" in out["elevator_qty"]["src"]
    # 起诉状里验收台数带待核实标记，不是直接写「2台电梯」
    assert "⚠️ 待核实：2台电梯均于" in render_complaint(fields)


@pytest.mark.parametrize(
    "clause",
    ["本合同不采用银行保函，质保金为合同价款5%，质保期满后支付", "剩余5%质保金待乙方提交银行保函后支付"],
)
def test_cash_retention_with_guarantee_wording_still_asks_lawyer(clause):
    import asyncio

    from app.agent import nodes

    fields = apply_derived_fields(_f(retention_ratio="5%", retention_clause_text=clause))
    assert "保函" not in fields["payable_ratio"]["src"]
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
    assert asked and asked[0]["pending"]["field_keys"] == ["payable_ratio"]


# 约定合同签订地法院：按合同盖章页「合同签订地」推断法院（新案卷核对，买卖合同）
_SIGN_DISPUTE = "因本合同或本合同相关事项发生的纠纷，应向本合同签订地有管辖权人民法院依法裁决。"


@pytest.mark.parametrize(
    ("place", "court", "text"),
    [
        (
            "河北省保定市莲池区朝阳南大街1号",
            "保定市莲池区",
            "合同签订地为河北省保定市莲池区朝阳南大街1号，故原告向保定市莲池区人民法院提起诉讼。",
        ),
        # 签订地没写区县：法院留待补充
        ("某某项目部", None, "合同签订地为某某项目部，故原告向【待补充】人民法院提起诉讼。"),
        # 没抽到签订地
        (None, None, "合同签订地为【待补充】，故原告向【待补充】人民法院提起诉讼。"),
    ],
)
def test_sign_place_jurisdiction(place, court, text):
    fields = apply_derived_fields(_f(dispute_clause_text=_SIGN_DISPUTE, contract_sign_place=place))
    assert fields["court_district"]["value"] == court
    assert fields["jurisdiction_text"]["value"] == text
    assert "待核实" in fields["jurisdiction_text"]["src"] or court is None
