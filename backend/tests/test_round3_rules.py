# 第三轮《起诉状规则确认单》的规则：违约金措辞、已付 / 欠款比例或算式、交货地管辖、
# 独立公司做原告、总公司写职务、仲裁措辞、管辖依据全称、原告信息表重复行合并。数据均为虚构。
import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import config
from app.config import PLAINTIFF_HQ_NAME as HQ
from app.config import PLAINTIFF_HQ_REP_TITLE
from app.services import branch_registry
from app.services import branch_table as bt
from app.services.complaint_renderer import fill_status, render_complaint
from app.services.derived_fields import apply_derived_fields


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """虚构的原告信息表：总公司、一家分公司、一家独立工程公司、一家名称以总公司开头的公司。"""
    entries = [
        {
            "name": HQ,
            "credit_code": "HQ-CODE",
            "representative": "总甲",
            "address": "总部",
        },
        {
            "name": HQ + "甲分公司",
            "credit_code": "A",
            "representative": "分乙",
            "address": "甲地",
        },
        {
            "name": "某市日立电梯工程有限公司",
            "credit_code": "B",
            "representative": "工丙",
            "address": "乙地",
        },
        {
            "name": HQ + "某州工程有限公司",
            "credit_code": "C",
            "representative": "工丁",
            "address": "丙地",
        },
    ]
    path = tmp_path / "branch_info.json"
    path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(branch_registry, "BRANCH_INFO_PATH", str(path))
    return path


# ── 第 4 题：违约金 / 逾期付款利息 ────────────────────────────────────────────


def test_penalty_clause_writes_penalty_and_cites_clause():
    fields = apply_derived_fields(
        _f(
            breach_interest_clause_location="第11.4条",
            breach_clause_text="甲方逾期付款的，每日按未付款的万分之零点五支付违约金。",
            breach_interest_rate_text="日万分之零点五的利率",
        )
    )
    assert fields["interest_term"]["value"] == "违约金"
    assert fields["breach_clause_sentence"]["value"] == (
        "依据合同第11.4条约定，甲方逾期付款的，每日按未付款的万分之零点五支付违约金。"
    )
    text = render_complaint(
        fields,
        template="2、支付{{interest_term}}（按照{{interest_rate_basis}}）\n"
        "{{breach_clause_sentence}}被告应支付{{interest_term}}。",
    )
    assert text.startswith("2、支付违约金（按照⚠️ 待核实：日万分之零点五的利率）")
    assert "⚠️ 待核实：依据合同第11.4条约定" in text
    assert text.endswith("被告应支付违约金。")


def test_no_penalty_keeps_interest_wording_and_drops_citation():
    fields = _f(breach_interest_rate_text="年利率6%的标准")
    template = "{{breach_clause_sentence}}被告应支付{{interest_term}}。"
    assert render_complaint(fields, template=template) == "被告应支付逾期付款利息。"
    # 空着的可选句不算【待补充】，也不计入就绪度
    keys, missing = fill_status(fields, template=template)
    assert keys == [] and missing == set()
    marked = render_complaint(fields, template=template, mark_fills=True)
    assert "⟦⟧" not in marked


def test_penalty_without_clause_text_asks_lawyer():
    fields = _f(breach_interest_rate_text="每日万分之五的违约金标准")
    template = "{{breach_clause_sentence}}"
    assert "【待补充】" in render_complaint(fields, template=template)
    _, missing = fill_status(fields, template=template)
    assert missing == {"breach_clause_sentence"}


# ── 第 6 题：已付 / 欠款写比例或算式 ─────────────────────────────────────────

_AMOUNT_TEMPLATE = "仅支付了¥{{paid_amount}}元{{paid_note}}，尚欠¥{{unpaid_amount}}元{{unpaid_note}}未付"


def test_amounts_summing_to_total_use_ratio():
    fields = _f(total_amount=1000000, paid_amount=800000, unpaid_amount=200000)
    assert render_complaint(fields, template=_AMOUNT_TEMPLATE) == (
        "仅支付了¥800000元（占合同款的80%），尚欠¥200000元（占合同款的20%）未付"
    )


def test_amounts_not_summing_to_total_use_formula():
    # 还有未到期的质保金：已付 + 本次欠款 ≠ 总价 → 写「（到期金额-已付）」
    fields = _f(total_amount=1000000, paid_amount=700000, unpaid_amount=250000)
    text = render_complaint(fields, template=_AMOUNT_TEMPLATE)
    # 金额勾稽不通过，金额本身照旧标冲突，提醒律师核对
    assert text == (
        "仅支付了¥【高亮冲突：700000】元，尚欠¥【高亮冲突：250000】元"
        "⚠️ 待核实：（950000-700000）未付"
    )


def test_missing_amount_writes_no_note():
    fields = _f(total_amount=1000000, unpaid_amount=200000)
    text = render_complaint(fields, template=_AMOUNT_TEMPLATE)
    assert text == "仅支付了¥【待补充】元，尚欠¥200000元未付"


def test_lawyer_edited_note_is_kept():
    fields = _f(total_amount=1000000, paid_amount=800000, unpaid_amount=200000)
    fields["unpaid_note"] = {"value": "（1000000-800000）", "src": "律师人工确认修改"}
    assert apply_derived_fields(fields)["unpaid_note"]["value"] == "（1000000-800000）"


# ── 第 9 题：交货地管辖 ──────────────────────────────────────────────────────


def test_delivery_place_with_district_is_adopted():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向交货地点所在地人民法院起诉",
            delivery_place="江苏省苏州市某某区某路1号工地",
            project_site="某某项目",
        )
    )
    assert fields["court_district"]["value"] == "苏州市某某区"
    assert fields["jurisdiction_text"]["value"] == (
        "因交货地点为江苏省苏州市某某区某路1号工地，属苏州市某某区法院辖区，"
        "故原告向苏州市某某区人民法院提起诉讼。"
    )


def test_delivery_place_without_district_infers_from_install_address():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向交货地点所在地人民法院起诉",
            delivery_place="某某项目C地块工地",
            install_address="江苏省苏州市某某区某路121号某小区",
        )
    )
    node = fields["court_district"]
    assert node["value"] == "苏州市某某区"
    assert "验收报告安装地点" in node["src"] and "待核实" in node["src"]


def test_delivery_place_not_inferable_is_left_to_lawyer():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向交货地点所在地人民法院起诉",
            delivery_place="某某项目C地块工地",
            install_address="开发区某路121号某小区",
        )
    )
    assert fields["court_district"]["value"] is None
    assert fields["addressee"]["value"] == "【待补充】人民法院"


def test_site_without_district_also_uses_install_address():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地人民法院起诉",
            project_site="某某花园项目",
            install_address="南京市某某区某路8号",
        )
    )
    assert fields["court_district"]["value"] == "南京市某某区"
    assert fields["jurisdiction_text"]["value"].startswith("因工程所在地为某某花园项目")


# ── 第 16 题：管辖依据写法律全称 ─────────────────────────────────────────────


def test_general_jurisdiction_cites_full_statute_name():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="双方可向人民法院提起诉讼",
            defendant_address="南京市某某区某路1号",
        )
    )
    assert fields["jurisdiction_text"]["value"].startswith(
        "依据《中华人民共和国民事诉讼法》第二十四条，"
    )


# ── 第 12、13 题：独立公司做原告；只有总公司写职务 ────────────────────────────


def test_independent_company_is_plaintiff_with_legal_rep(registry):
    fields = apply_derived_fields(
        _f(
            contract_type="安装合同",
            plaintiff_branch_raw="集团/某市日立电梯工程有限公司",
        )
    )
    assert fields["plaintiff_name_final"]["value"] == "某市日立电梯工程有限公司"
    assert fields["plaintiff_rep_label"]["value"] == "法定代表人"
    assert (
        fields["plaintiff_person_in_charge"]["value"] == "工丙"
    )  # 不是总公司，不加职务
    assert fields["plaintiff_credit_code"]["value"] == "B"


def test_independent_company_short_name_resolved_from_table(registry):
    fields = apply_derived_fields(
        _f(
            contract_type="安装合同",
            plaintiff_branch_raw="集团/营销网络/某州工程有限公司",
        )
    )
    assert fields["plaintiff_name_final"]["value"] == HQ + "某州工程有限公司"
    assert "待核实" not in fields["plaintiff_name_final"]["src"]


def test_unknown_independent_company_is_marked_for_review(registry):
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="另一电梯工程有限公司")
    )
    node = fields["plaintiff_name_final"]
    assert node["value"] == "另一电梯工程有限公司"
    assert "待核实" in node["src"]


def test_empty_branch_falls_back_to_contract_party_b(registry):
    fields = apply_derived_fields(
        {
            "contract_type": {"value": "安装合同", "src": "《审批表》"},
            "contract_party_b": {
                "value": "某市日立电梯工程有限公司",
                "src": "《合同》盖章页",
            },
        }
    )
    assert fields["plaintiff_name_final"]["value"] == "某市日立电梯工程有限公司"
    assert fields["plaintiff_rep_label"]["value"] == "法定代表人"


def test_branch_keeps_person_in_charge_without_title(registry):
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="集团/甲分公司")
    )
    assert fields["plaintiff_rep_label"]["value"] == "负责人"
    assert fields["plaintiff_person_in_charge"]["value"] == "分乙"


def test_headquarters_rep_gets_title(registry):
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    assert fields["plaintiff_rep_label"]["value"] == "法定代表人"
    assert (
        fields["plaintiff_person_in_charge"]["value"]
        == f"总甲，{PLAINTIFF_HQ_REP_TITLE}"
    )
    # 再跑一遍不重复加职务
    again = apply_derived_fields(fields)
    assert (
        again["plaintiff_person_in_charge"]["value"]
        == f"总甲，{PLAINTIFF_HQ_REP_TITLE}"
    )


# ── 第 15 题：仲裁申请书措辞 ────────────────────────────────────────────────


def test_arbitration_wording_in_full_template():
    fields = _f(dispute_clause_text="提交南京仲裁委员会仲裁")
    text = render_complaint(fields)
    assert "自申请仲裁之日起" in text and "自起诉之日起" not in text
    assert "申请人特将此案提请贵委仲裁，祈裁如所请。" in text
    assert "贵院" not in text and "祈判" not in text


# ── 原告信息表：完全相同的重复行自动合并 ─────────────────────────────────────


def test_dedupe_identical_rows_only():
    row = bt.normalize_entry({"name": "甲公司", "address": "甲地"})
    other = bt.normalize_entry({"name": "甲公司", "address": "乙地"})
    kept, warnings = bt.dedupe_entries([row, dict(row), other])
    assert kept == [row, other]
    assert warnings == ["「甲公司」重复出现且内容完全相同，已自动合并为一行"]
    assert bt.check_entries(kept).errors  # 内容不同的同名行仍阻止保存


def test_preview_merges_identical_rows(tmp_path, monkeypatch):
    from app.main import app

    monkeypatch.setattr(config, "BRANCH_INFO_PATH", str(tmp_path / "t.json"))
    monkeypatch.setattr(config, "BRANCH_HISTORY_DIR", str(tmp_path / "hist"))
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-token")
    book = Workbook()
    book.active.append(["原告名称", "统一社会信用代码", "住所地", "负责人"])
    for _ in range(2):
        book.active.append(["甲公司", "91440000MA00000015", "某地址", "张三"])
    buf = io.BytesIO()
    book.save(buf)
    res = TestClient(app).post(
        "/api/branches/preview",
        files={"file": ("t.xlsx", buf.getvalue())},
        headers={"X-Admin-Token": "admin-token"},
    )
    body = res.json()
    assert res.status_code == 200 and body["errors"] == []
    assert len(body["entries"]) == 1
    assert any("自动合并" in w for w in body["warnings"])
