# 签约日期试点结构化出处（#60）：规则按锚定得到的实际来源文件判断，不从模型写的 src 猜。虚构材料。
from app.agent.nodes import _needs_signature_page
from app.services.complaint_renderer import render_complaint
from app.services.extraction import apply_strategic_guards, enrich_provenance, field_source_file

MAIN = {"filename": "虚构安装合同.pdf", "identified_type": "合同", "is_scanned": False,
        "text": "电梯安装合同\n签订日期：2024年3月8日", "pages": [{"page": 2, "text": "签订日期：2024年3月8日"}]}
STRATEGIC = {"filename": "虚构战略合作协议.pdf", "identified_type": "战略合同", "is_scanned": False,
             "text": "战略合作协议\n签订日期：2019年1月1日", "pages": [{"page": 1, "text": "签订日期：2019年1月1日"}]}
APPROVAL = {"filename": "虚构审批表.pdf", "identified_type": "审批表", "is_scanned": False,
            "text": "签约时间\n2024年3月6日", "pages": [{"page": 1, "text": "签约时间\n2024年3月6日"}]}
FILES = [APPROVAL, MAIN, STRATEGIC]


def _run(value, src):
    fields = {"contract_sign_date": {"value": value, "src": src}}
    enrich_provenance(fields, FILES)
    apply_strategic_guards(fields, FILES)
    return fields


def test_main_contract_hit_is_normal():
    node = _run("2024年3月8日", "《虚构安装合同.pdf》盖章页")["contract_sign_date"]
    assert node["source_file"] == "虚构安装合同.pdf" and node["verified"] is True and node["page"] == 2
    assert "待核实" not in node["src"]
    assert "2024年3月8日" in render_complaint({"contract_sign_date": node})


def test_strategic_only_hit_cleared_even_with_wrong_filename():
    # 模型把出处写成主合同，值实际只在战略合作协议里：不能当本案签约日期
    fields = _run("2019年1月1日", "《虚构安装合同.pdf》盖章页")
    assert fields["contract_sign_date"]["value"] is None
    assert "【待补充】" in render_complaint(fields)


def test_strategic_hit_with_generic_or_empty_src_cleared():
    assert _run("2019年1月1日", "合同签订页")["contract_sign_date"]["value"] is None
    assert _run("2019年1月1日", "")["contract_sign_date"]["value"] is None


def test_generic_src_main_contract_hit_kept():
    node = _run("2024年3月8日", "合同签订页")["contract_sign_date"]
    assert node["value"] == "2024年3月8日" and node["source_file"] == "虚构安装合同.pdf"


def test_unanchored_value_kept_for_review_not_judged_by_model_src():
    # 原文哪里都找不到：不按模型写的「战略合作协议」清空，也不当正常值，标待核实交律师
    node = _run("2020年5月5日", "《虚构战略合作协议.pdf》签订页")["contract_sign_date"]
    assert node["value"] == "2020年5月5日"
    assert node["source_file"] is None and node["verified"] is False
    assert "待核实" in node["src"]
    assert "⚠️ 待核实：2020年5月5日" in render_complaint({"contract_sign_date": node})


def test_unanchored_new_extraction_not_cleared_before_anchoring():
    # 按 extract 节点的真实顺序：锚定前先把关一次，再锚定、再把关（#150 评审）。日期实际在主合同，
    # 模型却把出处写成战略合作协议：锚定前不能先清空，锚定后按实际来源保留
    fields = {"contract_sign_date": {"value": "2024年3月8日", "src": "《虚构战略合作协议.pdf》签订页"}}
    apply_strategic_guards(fields, FILES)
    assert fields["contract_sign_date"]["value"] == "2024年3月8日"
    enrich_provenance(fields, FILES)
    apply_strategic_guards(fields, FILES)
    node = fields["contract_sign_date"]
    assert node["value"] == "2024年3月8日" and node["source_file"] == "虚构安装合同.pdf"


def test_same_date_in_main_and_strategic_is_ambiguous_not_cleared():
    # 同一日期同等质量地命中主合同和战略合同：模型写哪份就选哪份只是打破平局，不算核实出的来源（#150 评审）
    both = dict(STRATEGIC, text="签订日期：2024年3月8日", pages=[{"page": 1, "text": "签订日期：2024年3月8日"}])
    files = [APPROVAL, MAIN, both]
    fields = {"contract_sign_date": {"value": "2024年3月8日", "src": "《虚构战略合作协议.pdf》"}}
    enrich_provenance(fields, files)
    apply_strategic_guards(fields, files)
    node = fields["contract_sign_date"]
    assert node["value"] == "2024年3月8日"
    assert node["source_file"] is None and node["verified"] is True
    assert set(node["source_candidates"]) == {"虚构安装合同.pdf", "虚构战略合作协议.pdf"}
    assert field_source_file(node) is None


def test_legacy_node_without_structured_source_reads_src():
    # 升级前的会话：字段里没有 source_file，读取来源时退回从 src 取文件名
    assert field_source_file({"value": "x", "src": "《虚构战略合作协议.pdf》"}) == "虚构战略合作协议.pdf"


def test_signature_page_needed_by_structured_source():
    party_b = {"contract_party_b": {"value": "虚构电梯公司", "src": "《虚构安装合同.pdf》"}}
    from_approval = _run("2024年3月6日", "《虚构审批表.pdf》签约时间") | party_b
    assert from_approval["contract_sign_date"]["source_file"] == "虚构审批表.pdf"
    assert _needs_signature_page(from_approval, FILES)
    from_contract = _run("2024年3月8日", "合同签订页") | party_b
    assert not _needs_signature_page(from_contract, FILES)
    # 旧数据：没有 source_file 时看 src 文字
    legacy = {"contract_sign_date": {"value": "2024年3月6日", "src": "审批表签约时间"}} | party_b
    assert _needs_signature_page(legacy, FILES)
    # 锚定过但原文没命中：即便模型写「合同签订页」，也继续找签章页（#150 评审）
    unanchored = _run("2020年5月5日", "合同签订页") | party_b
    assert unanchored["contract_sign_date"]["source_file"] is None
    assert _needs_signature_page(unanchored, FILES)
