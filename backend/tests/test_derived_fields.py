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


def test_interest_basis_flags_contract_rate():
    fields = apply_derived_fields(
        {"breach_interest_rate_text": {"value": "每日万分之五", "src": "《合同》"}}
    )
    node = fields["interest_rate_basis"]
    assert node["value"] == LPR_INTEREST_BASIS
    assert "每日万分之五" in node["src"] and "待核实" in node["src"]


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
