# 争议条款只写「按战略合同执行」、战略合同没上传（#126）：抽出转引句与条款位置，文书类型 / 管辖留待补充并提醒。
# 虚构条款。
from app.services.derived_fields import STRATEGIC_UNRESOLVED_NOTE, apply_derived_fields
from app.services.doc_search import locate_clause_bodies
from app.services.extraction import apply_strategic_dispute_ref, apply_strategic_guards
from app.services.validators import run_all_checks

_REF = "本合同的供货范围、货款结算和支付、违约责任、争议的解决与其他约定按“战略合同”内相关条款执行，其中："
TEXT_PAGE = (
    "电梯设计及供货合同\n就上述项目物料供应采购事宜，按下列条款签订：\n"
    "1. 以下文件为本合同的组成部分：（1）本合同书；（2）虚构集团战略合作合同文件（以下简称“战略合同”）。\n"
    f"2. {_REF}\n（1）付款方式见附件。\n3. 合同附件：合同清单。"
)
OCR_PAGE = (
    '<p data-bbox="87 118 367 129">就上述项目物料供应采购事宜，按下列条款签订：</p>\n'
    '<ol><li data-bbox="137 147 269 156">以下文件为本合同的组成部分:</li></ol>\n'
    '<p data-bbox="148 158 224 168">(1) 本合同书;</p>\n'
    f'<ol><li data-bbox="137 225 770 249">{_REF}</li></ol>\n'
    '<ol><li data-bbox="160 300 400 310">付款方式见附件。</li></ol>'
)


def _files(text, scanned=False):
    return [{"filename": "虚构供货合同.pdf", "identified_type": "合同", "is_scanned": scanned,
             "pages": [{"page": 1, "text": text}], "text": text}]


def _missing():
    return {
        "dispute_clause_text": {"value": None, "src": "《虚构供货合同.pdf》扫描件暂未识别"},
        "dispute_clause_location": {"value": None, "src": ""},
    }


def test_reference_in_numbered_list_item_extracted():
    fields = _missing()
    apply_strategic_dispute_ref(fields, _files(TEXT_PAGE))
    assert fields["dispute_clause_text"]["value"] == _REF
    assert fields["dispute_clause_location"]["value"] == "第2条"
    assert "推算" not in fields["dispute_clause_location"]["src"]


def test_ocr_list_without_numbers_counts_items_and_flags_guess():
    # OCR 把列表编号丢了，只剩 <ol><li>：按同一缩进的列表项数到第 2 项，标「推算，待核实」
    fields = _missing()
    apply_strategic_dispute_ref(fields, _files(OCR_PAGE, scanned=True))
    loc = fields["dispute_clause_location"]
    assert loc["value"] == "第2条"
    assert "推算" in loc["src"] and "待核实" in loc["src"] and "OCR" in loc["src"]
    assert "<" not in fields["dispute_clause_text"]["value"]


def test_unuploaded_strategic_leaves_kind_and_court_open_with_reminder():
    fields = _missing()
    apply_strategic_dispute_ref(fields, _files(TEXT_PAGE))
    fields = apply_derived_fields(fields)
    for key in ("document_kind", "court_district", "jurisdiction_text", "addressee"):
        assert fields[key]["value"] is None, key
        assert fields[key]["src"] == STRATEGIC_UNRESOLVED_NOTE
    check = next(c for c in run_all_checks(fields) if c.key == "strategic_dispute")
    assert check.applicable and check.needs_review and "上传战略合同" in check.message


def test_existing_dispute_clause_untouched():
    fields = {"dispute_clause_text": {"value": "向工程所在地人民法院提起诉讼。", "src": "《合同》第十条"}}
    apply_strategic_dispute_ref(fields, _files(TEXT_PAGE))
    assert fields["dispute_clause_text"]["value"] == "向工程所在地人民法院提起诉讼。"
    assert "dispute_clause_location" not in fields


def test_uploaded_strategic_still_read_from_strategic_contract():
    # 战略合同已上传：补上的转引句随后被清空、转去战略合同里读；读不到时同样不套被告住所地法院
    files = _files(TEXT_PAGE) + [{"filename": "虚构战略合作协议.pdf", "identified_type": "战略合同", "pages": []}]
    fields = _missing()
    apply_strategic_dispute_ref(fields, files)
    apply_strategic_guards(fields, files)
    assert fields["dispute_clause_text"]["value"] is None and fields["dispute_clause_text"]["strategic_ref"]
    assert fields["dispute_clause_location"]["value"] == "第2条"
    assert apply_derived_fields(fields)["court_district"]["src"] == STRATEGIC_UNRESOLVED_NOTE


def test_missing_dispute_clause_no_longer_claims_litigation_was_agreed():
    fields = apply_derived_fields(_missing())
    kind = fields["document_kind"]
    assert kind["value"] == "民事起诉状" and "未识别" in kind["src"] and "待核实" in kind["src"]
    assert not next(c for c in run_all_checks(fields) if c.key == "strategic_dispute").applicable


def test_reference_counts_as_dispute_found_for_ocr_stop():
    assert "dispute" in locate_clause_bodies([{"page": 1, "text": OCR_PAGE}])


def test_location_filled_when_model_extracted_reference_without_location():
    fields = {
        "dispute_clause_text": {"value": "争议的解决按战略合同执行", "src": "《虚构供货合同.pdf》"},
        "dispute_clause_location": {"value": None, "src": "未直接约定争议解决条款"},
    }
    apply_strategic_dispute_ref(fields, _files(TEXT_PAGE))
    assert fields["dispute_clause_location"]["value"] == "第2条"
    assert fields["dispute_clause_text"]["value"] == "争议的解决按战略合同执行"  # 模型抽到的原文不动


def test_reference_wrapped_across_lines_in_text_pdf():
    # 文字版 PDF 长句折行：「争议的解决与其他约定按“战略合同”」中间有换行也要认出，原文去掉折行
    page = TEXT_PAGE.replace("争议的解决与其他约定按", "争议的解决与其他约\n定按")
    fields = _missing()
    apply_strategic_dispute_ref(fields, _files(page))
    assert fields["dispute_clause_text"]["value"] == _REF
    assert fields["dispute_clause_location"]["value"] == "第2条"



def test_reference_mentioning_forum_words_is_still_unresolved():
    # 「按战略合同关于管辖 / 仲裁的条款执行」说的是战略合同的条款：仍是没读到的转引（#133 评审）
    for text in ("争议解决按战略合同关于管辖的条款执行。", "争议解决按战略合同关于仲裁的条款执行。",
                 "本合同争议的解决，按双方签订的《电梯战略合作协议》约定执行。"):
        fields = apply_derived_fields({"dispute_clause_text": {"value": text, "src": "《虚构供货合同.pdf》第2条"}})
        assert fields["document_kind"]["value"] is None, text
        assert fields["court_district"]["src"] == STRATEGIC_UNRESOLVED_NOTE, text
        assert next(c for c in run_all_checks(fields) if c.key == "strategic_dispute").needs_review, text


def test_main_contract_forum_outside_reference_still_counts():
    # 转引段之外主合同自己写了仲裁机构：按主合同的约定，不当未读到的转引
    text = "本合同其他事项按战略合同执行；争议提交虚构仲裁委员会仲裁。"
    fields = apply_derived_fields({"dispute_clause_text": {"value": text, "src": "《虚构供货合同.pdf》第9条"}})
    assert fields["document_kind"]["value"] == "仲裁申请书"


def test_adjacent_ocr_list_items_without_punctuation_not_merged():
    # 前一项「合同附件清单」没有句号 / 分号：只取命中的那一项（#133 评审）
    page = (
        '<ol><li data-bbox="137 100 300 110">合同附件清单</li></ol>\n'
        '<ol><li data-bbox="137 120 600 130">争议解决按战略合同执行。</li></ol>'
    )
    fields = _missing()
    apply_strategic_dispute_ref(fields, _files(page, scanned=True))
    assert fields["dispute_clause_text"]["value"] == "争议解决按战略合同执行。"
    assert fields["dispute_clause_location"]["value"] == "第2条"
