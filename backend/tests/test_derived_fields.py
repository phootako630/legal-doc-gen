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
        ("江苏省苏州市昆山市花桥镇", "苏州市昆山市"),
        ("山东省济南市市中区经十路", "济南市市中区"),
        ("位于南京市雨花台区", "南京市雨花台区"),
    ],
)
def test_derive_court_district(site, expected):
    assert derive_court_district(site) == expected


@pytest.mark.parametrize(
    "site", [None, "", "某某二期项目", "雨花台区某某二期", "某某大厦附近南京市"]
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
    assert fields["plaintiff_credit_code"]["src"] == "分公司信息表"


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
            "依据《民诉法》第24条，故原告向南京市秦淮区人民法院提起诉讼。",
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
    ("clause", "expected"),
    [
        ("验收合格、完整移交并办理完工程结算手续后支付", "已全部移交物业并办理结算"),
        ("验收合格后30日内支付至100%", "已全部移交物业"),
        (None, "已全部移交物业"),
    ],
)
def test_handover_text(clause, expected):
    fields = apply_derived_fields(_f(payment_clause_text=clause))
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
    assert "与合同盖章页乙方一致" in node["src"]

    fields = _f(
        contract_type="安装合同",
        plaintiff_branch_raw="集团/营销网络/湖南分公司",
        contract_party_b="日立电梯（中国）有限公司郴州分公司",
    )
    apply_derived_fields(fields)
    node = fields["plaintiff_name_final"]
    assert node["value"] == HQ + "郴州分公司"
    assert "以合同盖章页乙方名称为准" in node["src"] and "待核实" in node["src"]


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
