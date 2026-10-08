# Codex 复审发现的三处问题的回归测试（#29 交货地、#30 违约金、#31 职务）。数据均为虚构。
import json

import pytest

from app.config import PLAINTIFF_HQ_NAME as HQ
from app.services import branch_registry
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


# ── #29：交货地点缺失时不拿工程地点冒充 ─────────────────────────────────────

_DELIVERY = "向交货地点所在地人民法院起诉"


def test_missing_delivery_place_infers_from_install_address_not_project_site():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_DELIVERY,
            project_site="南京市甲区某路",
            install_address="苏州市乙区某路",
        )
    )
    court = fields["court_district"]
    assert court["value"] == "苏州市乙区"
    assert "合同交货地点未识别" in court["src"]
    assert "验收报告安装地点「苏州市乙区某路」推断" in court["src"]
    text = fields["jurisdiction_text"]["value"]
    assert "南京" not in text
    assert text == (
        "因交货地点为【待补充】，属苏州市乙区法院辖区"
        "【系统按验收报告安装地点「苏州市乙区某路」推断，待核实】，"
        "故原告向苏州市乙区人民法院提起诉讼。"
    )
    # 律师在最终文书里就能看到法院是系统推断及其依据
    rendered = render_complaint(fields, template="{{jurisdiction_text}}")
    assert rendered.startswith("⚠️ 待核实：") and "南京" not in rendered
    assert "系统按验收报告安装地点「苏州市乙区某路」推断" in rendered


def test_missing_delivery_place_without_install_address_is_left_blank():
    fields = apply_derived_fields(
        _f(dispute_clause_text=_DELIVERY, project_site="南京市甲区某路")
    )
    assert fields["court_district"]["value"] is None
    assert fields["addressee"]["value"] == "【待补充】人民法院"


def test_delivery_place_with_district_wins_over_other_places():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_DELIVERY,
            delivery_place="无锡市丙区某工地",
            project_site="南京市甲区某路",
            install_address="苏州市乙区某路",
        )
    )
    assert fields["court_district"]["value"] == "无锡市丙区"
    text = fields["jurisdiction_text"]["value"]
    assert "因交货地点为无锡市丙区某工地" in text and "推断" not in text


def test_project_site_without_district_also_notes_inference():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地人民法院起诉",
            project_site="某某花园项目",
            install_address="苏州市乙区某路",
        )
    )
    assert (
        "【系统按验收报告安装地点「苏州市乙区某路」推断，待核实】"
        in (fields["jurisdiction_text"]["value"])
    )


def test_lawyer_set_court_is_kept():
    fields = _f(dispute_clause_text=_DELIVERY, install_address="苏州市乙区某路")
    fields["court_district"] = {"value": "常州市丁区", "src": "律师人工确认修改"}
    fields = apply_derived_fields(fields)
    assert fields["court_district"]["value"] == "常州市丁区"
    assert "推断" not in fields["jurisdiction_text"]["value"]


def test_project_site_jurisdiction_unchanged():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地人民法院起诉",
            project_site="南京市甲区某路",
        )
    )
    assert fields["court_district"]["value"] == "南京市甲区"
    assert fields["jurisdiction_text"]["value"].startswith(
        "因工程所在地为南京市甲区某路"
    )


# ── #30：固定 / 一次性违约金不套用 LPR 持续计付 ─────────────────────────────


def _penalty(clause: str, rate: str | None = None) -> dict:
    fields = _f(
        total_amount=200000,
        paid_amount=100000,
        unpaid_amount=100000,
        breach_interest_clause_location="第11条",
        breach_clause_text=clause,
    )
    if rate:
        fields["breach_interest_rate_text"] = {"value": rate, "src": "《合同》第11条"}
    return fields


@pytest.mark.parametrize(
    "clause",
    [
        "甲方逾期付款的，应向乙方支付违约金人民币5000元",
        "甲方逾期付款的，应按合同总价的10%向乙方支付违约金",
    ],
)
def test_lump_sum_penalty_does_not_use_lpr(clause):
    text = render_complaint(_penalty(clause))
    assert "贷款市场报价利率" not in text
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert claim == (
        "2、判令被告向原告支付违约金（以¥100000元为基数，自起诉之日起，"
        "按照【待补充】计至实际付清之日止）；"
    )
    assert "违约金人民币5000元" in text or "合同总价的10%" in text


@pytest.mark.parametrize(
    "rate",
    [
        "人民币5000元",
        "逾期超过30日一次性支付5000元违约金",  # 「30日」是触发期限，不是计算周期
        "逾期超过30日按合同总价的10%一次性支付违约金",
        "合同总价的10%",
    ],
)
def test_lump_sum_rate_text_is_not_treated_as_periodic(rate):
    fields = apply_derived_fields(_penalty("甲方逾期付款的，应支付违约金", rate=rate))
    node = fields["interest_rate_basis"]
    assert node["value"] is None and "律师确认" in node["src"]
    text = render_complaint(fields)
    assert rate not in text.split("计至实际付清之日止")[0].split("按照")[-1]


@pytest.mark.parametrize(
    "rate",
    [
        "日万分之五的利率",
        "年利率6%的标准",
        "每逾期一日按未付款的0.05%",
        "按日0.5‰的标准",
    ],
)
def test_periodic_penalty_keeps_agreed_rate(rate):
    fields = _penalty("甲方逾期付款的，按约定支付违约金", rate=rate)
    text = render_complaint(fields)
    assert f"按照⚠️ 待核实：{rate}计至实际付清之日止" in text
    assert "自起诉之日起" in text


def test_plain_interest_still_defaults_to_lpr():
    text = render_complaint(_f(unpaid_amount=100000))
    assert "支付逾期付款利息" in text and "贷款市场报价利率" in text


# ── #31：信息表已写职务时不再追加默认职务 ───────────────────────────────────


@pytest.fixture
def table(tmp_path, monkeypatch):
    def write(rep: str, name: str = HQ):
        path = tmp_path / "branch_info.json"
        entry = {
            "name": name,
            "credit_code": "X",
            "representative": rep,
            "address": "a",
        }
        path.write_text(json.dumps({"entries": [entry]}, ensure_ascii=False), "utf-8")
        monkeypatch.setattr(branch_registry, "BRANCH_INFO_PATH", str(path))

    return write


def _hq_rep() -> dict:
    return apply_derived_fields(_f(contract_type="买卖合同"))[
        "plaintiff_person_in_charge"
    ]


@pytest.mark.parametrize(
    "rep",
    [
        "张三，总经理",
        "张三,总经理",
        "张三、总经理",
        "张三 总经理",
        "张三（总经理）",
        "张三(总经理)",
        "张三，副董事长",  # 比较完整职务，不按子串
        "张三，董事长兼总经理",
    ],
)
def test_existing_title_is_kept_and_flagged(table, rep):
    table(rep)
    node = _hq_rep()
    assert node["value"] == rep and not node["value"].endswith("，董事长")
    assert "待核实" in node["src"] and "《原告信息表》" in node["src"]
    text = render_complaint(
        apply_derived_fields(_f(contract_type="买卖合同")),
        template="{{plaintiff_person_in_charge}}",
    )
    assert text == f"⚠️ 待核实：{rep}"


@pytest.mark.parametrize("rep", ["张三，董事长", "张三（董事长）", "张三 董事长"])
def test_existing_default_title_not_duplicated(table, rep):
    table(rep)
    node = _hq_rep()
    assert node["value"] == rep and "待核实" not in node["src"]


@pytest.mark.parametrize("rep", ["網谷 憲晴", "张三 监事", "张三/董事长"])
def test_ambiguous_format_is_kept_and_flagged(table, rep):
    # 分不清是带空格的姓名还是「姓名 职务」：保留原文、不追加，提醒律师
    table(rep)
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    node = fields["plaintiff_person_in_charge"]
    assert node["value"] == rep
    assert "无法判断" in node["src"] and "待核实" in node["src"]
    again = apply_derived_fields(fields)
    assert again["plaintiff_person_in_charge"]["value"] == rep


@pytest.mark.parametrize("rep", ["张三", "網谷憲晴"])
def test_name_only_gets_default_title_idempotently(table, rep):
    table(rep)
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    assert fields["plaintiff_person_in_charge"]["value"] == f"{rep}，董事长"
    again = apply_derived_fields(fields)
    assert again["plaintiff_person_in_charge"]["value"] == f"{rep}，董事长"


def test_branch_gets_no_default_title(table):
    table("李四", name=HQ + "甲分公司")
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="集团/甲分公司")
    )
    assert fields["plaintiff_person_in_charge"]["value"] == "李四"


def test_lawyer_edited_rep_is_kept(table):
    table("张三")
    fields = _f(contract_type="买卖合同")
    fields["plaintiff_person_in_charge"] = {"value": "王五", "src": "律师人工确认修改"}
    assert apply_derived_fields(fields)["plaintiff_person_in_charge"]["value"] == "王五"
