# 合同甲方是「项目经理部 / 项目部」时，被告写所属公司并标待核实（确认单（六）第 3 题，#139）。虚构名称。
from app.services.derived_fields import apply_derived_fields


def _defendant(party_a, approval=None):
    fields = {"contract_party_a": {"value": party_a, "src": "《虚构合同》盖章页甲方"}}
    if approval is not None:
        fields["defendant_name"] = {"value": approval, "src": "《虚构审批表》合同买方名称"}
    return apply_derived_fields(fields)["defendant_name"]


def test_project_manager_dept_sues_owner_company():
    node = _defendant("虚构路桥第三工程有限公司虚构港项目经理部")
    assert node["value"] == "虚构路桥第三工程有限公司"
    assert "虚构港项目经理部" in node["src"] and "待核实" in node["src"]


def test_project_dept_with_plain_company_suffix():
    node = _defendant("虚构建设公司虚构苑二期项目部")
    assert node["value"] == "虚构建设公司"
    assert "待核实" in node["src"]


def test_group_and_branch_names_cut_at_last_company():
    assert _defendant("虚构建工集团有限公司虚构分公司虚构湾项目管理部")["value"] == "虚构建工集团有限公司虚构分公司"


def test_unclear_company_keeps_original_name():
    node = _defendant("虚构城市更新虚构湾项目部")
    assert node["value"] == "虚构城市更新虚构湾项目部"
    assert "切不清" in node["src"] and "待核实" in node["src"]


def test_company_word_inside_project_name_is_ambiguous():
    # 项目名称里也有「公司」：分不清哪段是所属公司，不自动截取（#144 评审）
    node = _defendant("虚构建设有限公司某某公司总部项目经理部")
    assert node["value"] == "虚构建设有限公司某某公司总部项目经理部"
    assert "切不清" in node["src"] and "待核实" in node["src"]


def test_ordinary_company_unaffected():
    node = _defendant("虚构置业有限公司", approval="虚构置业有限公司")
    assert node["value"] == "虚构置业有限公司"
    assert "待核实" not in node["src"] and "项目部" not in node["src"]


def test_company_name_containing_project_word_unaffected():
    # 名称里有「项目」但不以项目部结尾：不是项目部
    assert _defendant("虚构项目管理有限公司")["value"] == "虚构项目管理有限公司"


def test_approval_fallback_also_sues_owner():
    # 合同甲方读不到、沿用审批表名称时同样处理
    fields = {"defendant_name": {"value": "虚构路桥第三工程有限公司虚构港项目经理部", "src": "《虚构审批表》"}}
    node = apply_derived_fields(fields)["defendant_name"]
    assert node["value"] == "虚构路桥第三工程有限公司"
    assert node["src"] == (
        "《虚构审批表》；审批表买方名称为「虚构路桥第三工程有限公司虚构港项目经理部」（项目部），"
        "按律师规则起诉所属公司，请办案律师核实，待核实"
    )
    assert "合同甲方" not in node["src"]
    assert node["approval_value"] == "虚构路桥第三工程有限公司虚构港项目经理部"


def test_contract_party_a_kept_verbatim():
    fields = {"contract_party_a": {"value": "虚构路桥第三工程有限公司虚构港项目经理部", "src": "《虚构合同》"}}
    f = apply_derived_fields(fields)
    assert f["contract_party_a"]["value"] == "虚构路桥第三工程有限公司虚构港项目经理部"
