# 条款号写法兜底（#142）：不依赖模型这次写「第十二条」还是「第12.1条」。虚构条款。
from app.services.clause_location import refine_clause_locations

_PAGE = """第十二条 争议解决
12.1 所有因本合同引起的争议，双方协商不成的，提交虚构仲裁委员会仲裁。
12.2 仲裁期间合同其他部分继续履行。
第六条 付款方式
6.1 甲方按照本合同第6条之规定，于验收合格后30日内支付至合同总价的95%。"""


def _files(text=_PAGE):
    return [{"filename": "虚构合同.pdf", "identified_type": "合同", "is_scanned": True,
             "pages": [{"page": 3, "text": text}], "text": text}]


def _fields(loc, key="dispute", clause="所有因本合同引起的争议，双方协商不成的，提交虚构仲裁委员会仲裁。"):
    return {
        f"{key}_clause_location": {"value": loc, "src": "《虚构合同.pdf》第3页（OCR识别，请核实）"},
        f"{key}_clause_text": {"value": clause, "src": "《虚构合同.pdf》第3页（OCR识别，请核实）"},
    }


def test_chinese_article_refined_to_sub_item():
    fields = _fields("第十二条")
    refine_clause_locations(fields, _files())
    loc = fields["dispute_clause_location"]
    assert loc["value"] == "第12.1条"
    assert "原写作「第十二条」" in loc["src"] and "OCR" in loc["src"]


def test_arabic_article_refined_to_sub_item():
    fields = _fields("第12条")
    refine_clause_locations(fields, _files())
    assert fields["dispute_clause_location"]["value"] == "第12.1条"


def test_mismatched_number_unchanged():
    fields = _fields("第十一条")
    refine_clause_locations(fields, _files())
    assert fields["dispute_clause_location"]["value"] == "第十一条"


def test_chinese_article_to_arabic_when_contract_uses_arabic():
    # 付款条款原文没有紧跟子项编号（模型摘了中间一段），但合同用阿拉伯数字编子项 →「第6条」
    clause = "于验收合格后30日内支付至合同总价的95%"
    fields = _fields("第六条", key="payment", clause=clause)
    refine_clause_locations(fields, _files())
    assert fields["payment_clause_location"]["value"] == "第6条"
    assert "合同用阿拉伯数字编条款" in fields["payment_clause_location"]["src"]


def test_payment_sub_item_refined():
    clause = "甲方按照本合同第6条之规定，于验收合格后30日内支付至合同总价的95%。"
    fields = _fields("第六条", key="payment", clause=clause)
    refine_clause_locations(fields, _files())
    assert fields["payment_clause_location"]["value"] == "第6.1条"


def test_chinese_numbering_only_unchanged():
    page = "第六条 付款方式\n甲方于验收合格后30日内支付至合同总价的95%。"
    fields = _fields("第六条", key="payment", clause="甲方于验收合格后30日内支付至合同总价的95%。")
    refine_clause_locations(fields, _files(page))
    assert fields["payment_clause_location"]["value"] == "第六条"


def test_lawyer_edit_and_combined_locations_untouched():
    fields = _fields("第十二条")
    fields["dispute_clause_location"]["src"] = "律师确认修改"
    refine_clause_locations(fields, _files())
    assert fields["dispute_clause_location"]["value"] == "第十二条"
    fields = _fields("第2条及战略合同第十二条")
    refine_clause_locations(fields, _files())
    assert fields["dispute_clause_location"]["value"] == "第2条及战略合同第十二条"


def test_duplicate_openings_left_alone():
    # 12.1 与 12.2 开头相同：命中不止一处，分不清摘自哪一项，不改（#147 评审）
    page = ("第十二条 付款\n12.1 甲方应于合同签订后七日内向乙方支付合同总价款的百分之十作为预付款。\n"
            "12.2 甲方应于合同签订后七日内向乙方支付合同总价款的百分之十作为预付款，另付质保金。")
    fields = _fields("第十二条", clause="甲方应于合同签订后七日内向乙方支付合同总价款的百分之十作为预付款，另付质保金。")
    refine_clause_locations(fields, _files(page))
    # 不细化到子项；合同用阿拉伯数字编子项，只把条号写成阿拉伯数字
    assert fields["dispute_clause_location"]["value"] == "第12条"


def test_sub_number_must_start_the_line():
    page = "第十二条 争议解决\n详见附件12.1所有因本合同引起的争议，双方协商不成的，提交虚构仲裁委员会仲裁。"
    fields = _fields("第十二条")
    refine_clause_locations(fields, _files(page))
    assert fields["dispute_clause_location"]["value"] == "第十二条"


def test_unmatched_source_file_left_alone():
    # 出处里的文件名对不上任何上传文件：不拿别的合同当依据（#147 评审）
    fields = _fields("第十二条")
    fields["dispute_clause_text"]["src"] = "《另一份合同.pdf》第3页"
    refine_clause_locations(fields, _files())
    assert fields["dispute_clause_location"]["value"] == "第十二条"


def test_arabic_evidence_only_from_source_contract():
    # 本合同全用中文编号，另一份合同里有「6.1」：不改（#147 评审）
    own = "第六条 付款方式\n甲方于验收合格后30日内支付至合同总价的95%。"
    other = {"filename": "虚构补充合同.pdf", "identified_type": "合同", "text": "6.1 另一份合同的付款", "pages": []}
    fields = _fields("第六条", key="payment", clause="甲方于验收合格后30日内支付至合同总价的95%。")
    refine_clause_locations(fields, _files(own) + [other])
    assert fields["payment_clause_location"]["value"] == "第六条"


def test_only_cited_page_searched():
    # 出处写了第3页：只在第3页找；同样的原文在第5页前有「12.1」不算
    files = [{"filename": "虚构合同.pdf", "identified_type": "合同", "is_scanned": True, "pages": [
        {"page": 3, "text": "第十二条\n所有因本合同引起的争议，双方协商不成的，提交虚构仲裁委员会仲裁。"},
        {"page": 5, "text": "12.1 所有因本合同引起的争议，双方协商不成的，提交虚构仲裁委员会仲裁。"},
    ]}]
    fields = _fields("第十二条")
    refine_clause_locations(fields, files)
    assert fields["dispute_clause_location"]["value"] == "第12条"  # 不细化成第5页的「12.1」
