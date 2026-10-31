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
MAIN = {
    "filename": "虚构买卖合同.pdf", "identified_type": "合同", "is_scanned": True, "file_id": "m",
    "pages": [{"page": 1, "text": "合同首页"}, {"page": 3, "text": "2、争议的解决按战略合同执行。"}],
}


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
    # 主合同的转引条款保留，出处改到主合同转引句所在页（#146 评审）
    assert fields["dispute_clause_location"] == {
        "value": "第2条", "src": "《虚构买卖合同.pdf》第3页争议解决转引战略合同（OCR识别，请核实）"
    }


def test_main_ref_not_found_marked_for_review():
    fields = _dispute(SUPPLEMENT["filename"])
    main = {**MAIN, "pages": [{"page": 1, "text": "合同首页"}]}
    prefer_strategic_original(fields, [main, ORIGINAL, SUPPLEMENT])
    assert fields["dispute_clause_location"]["value"] == "第2条"
    assert fields["dispute_clause_location"]["src"].endswith("待核实")


def test_mixed_sources_location_from_supplement_cleared():
    # 正文取自原文、位置取自补充协议：位置也要以原文为准（#146 评审）
    fields = _dispute(ORIGINAL["filename"], loc="战略合同第三条")
    fields["dispute_clause_location"]["src"] = f"《{SUPPLEMENT['filename']}》"
    prefer_strategic_original(fields, [MAIN, ORIGINAL, SUPPLEMENT])
    assert fields["dispute_clause_text"]["value"] == "提交虚构仲裁委员会仲裁"
    assert fields["dispute_clause_location"]["value"] is None


def test_arbitration_institution_from_supplement_cleared():
    # 仲裁机构同样取自补充协议时一并清空，从原文刷新（#146 评审）
    fields = _dispute(SUPPLEMENT["filename"])
    fields["arbitration_institution"] = {"value": "虚构仲裁委员会", "src": f"《{SUPPLEMENT['filename']}》"}
    prefer_strategic_original(fields, [MAIN, ORIGINAL, SUPPLEMENT])
    assert fields["arbitration_institution"]["value"] is None
    kept = _dispute(ORIGINAL["filename"])
    kept["arbitration_institution"] = {"value": "虚构仲裁委员会", "src": f"《{ORIGINAL['filename']}》"}
    prefer_strategic_original(kept, [MAIN, ORIGINAL, SUPPLEMENT])
    assert kept["arbitration_institution"]["value"] == "虚构仲裁委员会"


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
