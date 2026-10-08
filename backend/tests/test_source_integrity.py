# 出处与派生值的可信度：原文未命中标待核实、来源文件与页码一致、换原告不残留旧信息、
# 合同台数保留 OCR 标记。数据均为虚构。
import json

from app.config import PLAINTIFF_HQ_NAME as HQ
from app.services import branch_registry
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields
from app.services.extraction import UNANCHORED_NOTE, enrich_provenance

_APPROVAL = {
    "filename": "审批表.pdf",
    "is_scanned": False,
    "pages": [{"page": 1, "text": "合同买方名称 甲公司 合同总额 894,934.00"}],
}
_CONTRACT = {
    "filename": "合同.pdf",
    "is_scanned": True,
    "pages": [
        {"page": 1, "text": "封面"},
        {"page": 2, "text": "工程地点：南京市雨花台区某路 合计 14 台"},
    ],
}


# ── 规范 1：原文未命中的值标「待核实」 ────────────────────────────────────────


def test_unanchored_value_is_marked_for_review():
    # 材料里只有「甲公司」，模型输出了不存在的公司名
    fields = {
        "defendant_name": {"value": "乙某某有限公司", "src": "《审批表.pdf》买方名称"},
        "total_amount": {"value": 894934, "src": "《审批表.pdf》合同总额"},
    }
    enrich_provenance(fields, [_APPROVAL])
    assert fields["defendant_name"]["src"].endswith(UNANCHORED_NOTE)
    assert fields["defendant_name"]["page"] is None
    assert UNANCHORED_NOTE not in fields["total_amount"]["src"]  # 命中的值不受影响


def test_unanchored_value_renders_as_needs_review():
    fields = {"defendant_name": {"value": "乙某某有限公司", "src": "《审批表.pdf》"}}
    enrich_provenance(fields, [_APPROVAL])
    text = render_complaint(fields, template="被告：{{defendant_name}}")
    assert text == "被告：⚠️ 待核实：乙某某有限公司"


def test_note_is_recomputed_after_ocr():
    # 第一次锚定时合同还没 OCR → 未命中；OCR 补读后再锚定命中 → 标记应去掉，且不重复追加
    fields = {"project_site": {"value": "南京市雨花台区某路", "src": "《合同.pdf》"}}
    enrich_provenance(fields, [_APPROVAL])
    enrich_provenance(fields, [_APPROVAL])
    assert fields["project_site"]["src"].count(UNANCHORED_NOTE) == 1
    enrich_provenance(fields, [_APPROVAL, _CONTRACT])
    assert UNANCHORED_NOTE not in fields["project_site"]["src"]
    assert fields["project_site"]["page"] == 2


def test_summary_fields_are_not_flagged():
    # AI 归纳的付款条款本就不是原文摘抄，不按「原文未找到」处理
    fields = {"payment_clause_summary": {"value": "验收后付清", "src": "《合同.pdf》"}}
    enrich_provenance(fields, [_APPROVAL])
    assert UNANCHORED_NOTE not in fields["payment_clause_summary"]["src"]


# ── 规范 2：来源文件名与页码来自同一文件 ─────────────────────────────────────


def test_source_file_follows_actual_hit():
    # 模型写「审批表」，实际只在合同第 2 页命中 → 来源改为合同，不出现「审批表第 2 页」
    fields = {
        "project_site": {
            "value": "南京市雨花台区某路",
            "src": "《审批表.pdf》工程地址栏",
        }
    }
    enrich_provenance(fields, [_APPROVAL, _CONTRACT])
    node = fields["project_site"]
    assert node["page"] == 2
    assert node["src"].startswith("《合同.pdf》")
    assert "审批表" not in node["src"]
    assert "OCR" in node["src"]  # 合同是扫描件：仍须提醒核实


# ── 需求 1：更换原告后不残留旧原告信息 ───────────────────────────────────────


def test_changing_plaintiff_clears_previous_registry_values(tmp_path, monkeypatch):
    table = tmp_path / "branch_info.json"
    table.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "name": HQ + "甲分公司",
                        "credit_code": "JIA-CODE",
                        "representative": "甲负责人",
                        "address": "甲地址",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(branch_registry, "BRANCH_INFO_PATH", str(table))
    fields = apply_derived_fields(
        {
            "contract_type": {"value": "安装合同", "src": "《审批表》"},
            "plaintiff_branch_raw": {"value": "集团/甲分公司", "src": "《审批表》"},
        }
    )
    assert fields["plaintiff_credit_code"]["value"] == "JIA-CODE"

    # 律师在审核页把原告改成表里没有的乙分公司
    fields["plaintiff_name_final"] = {
        "value": HQ + "乙分公司",
        "src": "律师人工确认修改",
    }
    fields = apply_derived_fields(fields)
    for key in (
        "plaintiff_credit_code",
        "plaintiff_person_in_charge",
        "plaintiff_address",
    ):
        assert fields[key]["value"] is None, key

    text = render_complaint(
        fields, template="{{plaintiff_name_final}}|{{plaintiff_credit_code}}"
    )
    assert text == f"{HQ}乙分公司|【待补充】"


def test_lawyer_edited_registry_value_is_kept(tmp_path, monkeypatch):
    # 律师自己填的信用代码不因原告查不到而被清掉
    monkeypatch.setattr(
        branch_registry, "BRANCH_INFO_PATH", str(tmp_path / "none.json")
    )
    fields = apply_derived_fields(
        {
            "plaintiff_name_final": {
                "value": HQ + "乙分公司",
                "src": "律师人工确认修改",
            },
            "plaintiff_credit_code": {"value": "YI-CODE", "src": "律师人工确认修改"},
        }
    )
    assert fields["plaintiff_credit_code"]["value"] == "YI-CODE"


# ── 需求 3：派生的合同台数保留 OCR 标记 ──────────────────────────────────────


def test_contract_qty_keeps_ocr_provenance():
    fields = {
        "elevator_qty_by_contract": {
            "value": 14,
            "src": "《合同.pdf》设备清单（OCR识别，请核实）",
            "page": 2,
            "anchor": "14",
            "channel": "ocr",
        }
    }
    node = apply_derived_fields(fields)["elevator_qty_contract"]
    assert node["value"] == 14
    assert node["page"] == 2 and node["channel"] == "ocr"
    assert "OCR" in node["src"]
    text = render_complaint(fields, template="安装{{elevator_qty_contract}}台")
    assert text == "安装⚠️ 待核实：14台"


def test_contract_qty_from_ocr_channel_without_note_gets_marked():
    # 来源说明里没写 OCR、但通道是 OCR（锚定在扫描件上命中）→ 补上提醒
    fields = {
        "elevator_qty_by_contract": {
            "value": 14,
            "src": "《合同.pdf》",
            "channel": "ocr",
        }
    }
    node = apply_derived_fields(fields)["elevator_qty_contract"]
    assert "OCR" in node["src"]
