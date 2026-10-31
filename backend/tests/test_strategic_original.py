# 争议条款转引战略合同时以战略合作合同原文为准，补充协议只作确认（确认单（六）第 5 题，#141）。虚构材料。
from app.agent.nodes import _clause_ocr_targets
from app.services.extraction import (
    STRATEGIC_ORIGINAL_NOTE,
    is_strategic_supplement,
    mark_strategic_supplement,
    prefer_strategic_original,
)
from app.services.validators import run_all_checks

ORIGINAL = {"filename": "虚构战略合作合同.pdf", "identified_type": "战略合同", "text": "战略合作合同", "is_scanned": False}
SUPPLEMENT = {
    "filename": "虚构战略合作协议补充协议（七）.pdf", "identified_type": "战略合同", "text": "补充协议", "is_scanned": False,
}
MAIN = {"filename": "虚构买卖合同.pdf", "identified_type": "合同", "is_scanned": True, "file_id": "m"}


def _dispute(filename, loc="第2条及战略合同第三条"):
    return {
        "dispute_clause_text": {"value": "提交虚构仲裁委员会仲裁", "src": f"《{filename}》"},
        "dispute_clause_location": {"value": loc, "src": f"《{filename}》"},
    }


def test_supplement_detected_by_title():
    assert is_strategic_supplement(SUPPLEMENT)
    assert not is_strategic_supplement(ORIGINAL)
    assert is_strategic_supplement({**ORIGINAL, "filename": "a.pdf", "text": "战略合作协议补充协议\n…"})


def test_original_read_before_supplement():
    targets = _clause_ocr_targets([MAIN, SUPPLEMENT, ORIGINAL])
    assert [f["filename"] for f in targets] == ["虚构买卖合同.pdf", "虚构战略合作合同.pdf", "虚构战略合作协议补充协议（七）.pdf"]


def test_both_uploaded_clause_from_supplement_is_cleared_for_original():
    fields = _dispute(SUPPLEMENT["filename"])
    prefer_strategic_original(fields, [MAIN, ORIGINAL, SUPPLEMENT])
    assert fields["dispute_clause_text"]["value"] is None and fields["dispute_clause_text"]["strategic_ref"]
    assert fields["dispute_clause_location"]["value"] == "第2条"  # 主合同的转引条款保留


def test_both_uploaded_clause_from_original_kept():
    fields = _dispute(ORIGINAL["filename"])
    prefer_strategic_original(fields, [MAIN, ORIGINAL, SUPPLEMENT])
    mark_strategic_supplement(fields, [MAIN, ORIGINAL, SUPPLEMENT])
    assert fields["dispute_clause_text"]["value"] == "提交虚构仲裁委员会仲裁"
    assert STRATEGIC_ORIGINAL_NOTE not in fields["dispute_clause_location"]["src"]


def test_only_supplement_uses_it_and_reminds():
    fields = _dispute(SUPPLEMENT["filename"])
    files = [MAIN, SUPPLEMENT]
    prefer_strategic_original(fields, files)
    mark_strategic_supplement(fields, files)
    assert fields["dispute_clause_location"]["value"] == "第2条及战略合同第三条"
    src = fields["dispute_clause_location"]["src"]
    assert STRATEGIC_ORIGINAL_NOTE in src and src.endswith("待核实")
    mark_strategic_supplement(fields, files)  # 重复运行不叠加
    assert fields["dispute_clause_location"]["src"].count(STRATEGIC_ORIGINAL_NOTE) == 1
    check = next(c for c in run_all_checks(fields) if c.key == "strategic_original")
    assert check.needs_review and not check.is_conflict and "上传原文" in check.message


def test_only_original_unchanged():
    fields = _dispute(ORIGINAL["filename"])
    prefer_strategic_original(fields, [MAIN, ORIGINAL])
    mark_strategic_supplement(fields, [MAIN, ORIGINAL])
    assert fields["dispute_clause_location"] == {"value": "第2条及战略合同第三条", "src": "《虚构战略合作合同.pdf》"}
    check = next(c for c in run_all_checks(fields) if c.key == "strategic_original")
    assert not check.applicable
