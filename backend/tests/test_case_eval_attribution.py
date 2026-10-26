# eval 错误归因（#108）：OCR 认错 / 抽取取错 / 其他页有 / 没读到 / 文字版 / 规则推导；数据为虚构
from scripts.case_eval.attribution import (
    EXTRACT,
    NOT_OCR,
    OCR_SUSPECT,
    OTHER_PAGE,
    RULE,
    UNREAD,
    attribute,
)
from scripts.case_eval.scoring import WRONG, score_field

_SCAN = "虚构安装合同.pdf"
_APPROVAL = "虚构审批表.pdf"


def _files(contract_pages, page_count=None):
    return [
        {"filename": _APPROVAL, "is_scanned": False, "text": "合同号 XG-0815 合同总额 300,000.00"},
        {
            "filename": _SCAN,
            "is_scanned": True,
            "page_count": page_count or len(contract_pages),
            "pages": [{"page": n, "text": t} for n, t in contract_pages],
        },
    ]


_PAGES = [(1, "电梯安装合同 第五条 争议解决 向工程所在地人民法院起诉"), (2, "签订日期：2024年3月8日 甲方（盖章）")]


def _node(value, page, src=f"《{_SCAN}》（扫描件，OCR识别，请核实）"):
    return {"value": value, "src": src, "page": page, "channel": "ocr"}


def test_ocr_misread():
    # 标准值 3月5日，OCR 把它认成了 3月8日：全部页都识别过、哪一页都找不到标准值
    assert attribute("contract_sign_date", "2024年3月5日", _node("2024年3月8日", 2), _files(_PAGES)) == OCR_SUSPECT


def test_extraction_error_on_cited_page():
    # OCR 文本里引用页就有「第五条」，系统却取成了别的
    assert attribute("dispute_clause_location", "第五条", _node("第四条", 1), _files(_PAGES)) == EXTRACT


def test_value_only_on_other_page():
    pages = [(1, "合同价款 300,000 元"), (2, "签订日期：2024年3月5日")]
    assert attribute("contract_sign_date", "2024年3月5日", _node("2024年3月6日", 1), _files(pages)) == OTHER_PAGE


def test_page_not_read_is_not_ocr_error():
    # 合同 6 页只识别了 2 页：找不到标准值可能只是没读到那一页
    node = _node(None, None)
    assert attribute("warranty_period", "24个月", node, _files(_PAGES, page_count=6)) == UNREAD


def test_text_layer_field():
    node = {"value": "XG-0816", "src": f"《{_APPROVAL}》合同号"}
    assert attribute("contract_no", "XG-0815", node, _files(_PAGES)) == NOT_OCR
    # 系统留空、但文字版材料里有标准值：同样不是 OCR 的问题
    assert attribute("contract_no", "XG-0815", None, _files(_PAGES)) == NOT_OCR


def test_rule_wording_not_attributed():
    # 管辖法院是规则拼出的措辞，原文里本就没有
    assert attribute("court_district", "南京市雨花台区人民法院", {"value": None}, _files(_PAGES)) == RULE


def test_literal_derived_field_still_attributed():
    # 被告名称虽由规则选定，值照抄合同盖章页：OCR 认错照样归因
    node = _node("虚构置业有眼公司", 2)
    pages = [(1, "甲方：虚构置业有眼公司")]
    assert attribute("defendant_name", "虚构置业有限公司", node, _files(pages)) == OCR_SUSPECT


def test_score_field_sets_cause_only_for_wrong_or_missing():
    files = _files(_PAGES)
    wrong = score_field("dispute_clause_location", "第五条", _node("第四条", 1), files)
    assert wrong.verdict == WRONG and wrong.cause == EXTRACT
    ok = score_field("dispute_clause_location", "第五条", _node("第五条", 1), files)
    assert ok.cause is None
    no_files = score_field("dispute_clause_location", "第五条", _node("第四条", 1))
    assert no_files.cause is None  # 没给材料（旧调用方式）不归因


# ── #115 评审回归 ─────────────────────────────────────────────────────────────
def test_empty_ocr_page_counts_as_read():
    # 两页都 OCR 过，其中一页识别为空：找不到标准值是 OCR 的问题，不是没读到
    files = _files([(1, "电梯安装合同"), (2, "")])
    assert attribute("contract_sign_date", "2024年3月5日", _node("2024年3月8日", 2), files) == OCR_SUSPECT


def test_ocr_field_not_masked_by_text_material():
    # OCR 字段的出处没带可解析的文件名，审批表里恰好有正确金额：仍按扫描件归因
    node = {"value": "306,000.00", "src": "合同第二条（OCR识别，请核实）", "channel": "ocr"}
    files = _files([(1, "合同总价为人民币306,000元")])
    assert attribute("total_amount", "300,000.00", node, files) == OCR_SUSPECT


def test_branch_table_fields_are_not_ocr():
    for key in ("plaintiff_credit_code", "plaintiff_address", "plaintiff_person_in_charge"):
        assert attribute(key, "91320100000000001Y", {"value": None}, _files(_PAGES)) == NOT_OCR


def test_every_derived_field_is_classified():
    # 新增派生字段时必须归入「照抄原文 / 原告信息表 / 规则措辞」之一，否则归因会错分
    from app.services.derived_fields import _DERIVERS
    from scripts.case_eval.attribution import BRANCH_TABLE_DERIVED, LITERAL_DERIVED, RULE_DERIVED

    groups = [LITERAL_DERIVED, BRANCH_TABLE_DERIVED, RULE_DERIVED]
    assert set().union(*groups) == {k for k, _, _ in _DERIVERS}
    assert sum(len(g) for g in groups) == len(set().union(*groups))  # 不重复归类
