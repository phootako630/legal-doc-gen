# 合同编号把关（#125）：审批表标题紧挨合同号栏时，模型会把「AH……-项目名」整段当合同号。
# 只保留「字母 + 数字」的编号本身；含中文又切不出编号的原样保留、标待核实，不猜。虚构编号。
from app.services.extraction import apply_rule_guards


def _guard(value, src="《虚构诉讼审批流程.pdf》合同号栏"):
    fields = {"contract_no": {"value": value, "src": src}}
    apply_rule_guards(fields)
    return fields["contract_no"]


def test_trailing_project_name_is_trimmed():
    node = _guard("AH9000001-虚构南翔路项目")
    assert node["value"] == "AH9000001"
    assert "项目名" in node["src"] and "待核实" not in node["src"]
    # 全角连字符、空格分隔同样处理
    assert _guard("AH9000001－虚构路项目")["value"] == "AH9000001"
    assert _guard("AH9000001 虚构路项目")["value"] == "AH9000001"


def test_plain_number_unchanged():
    node = _guard("AH9000001")
    assert node == {"value": "AH9000001", "src": "《虚构诉讼审批流程.pdf》合同号栏"}
    # 不含中文的其他编号格式（带横线的分段编号）不动
    assert _guard("XG-HT-2099-0731")["value"] == "XG-HT-2099-0731"


def test_unrecognized_number_with_chinese_kept_and_flagged():
    node = _guard("虚构项目合同第三号")
    assert node["value"] == "虚构项目合同第三号"
    assert "待核实" in node["src"]
    # 重复把关（抽取后与按需 OCR 后各跑一次）不重复追加说明
    fields = {"contract_no": dict(node)}
    apply_rule_guards(fields)
    assert fields["contract_no"]["src"].count("待核实") == 1


def test_missing_contract_no_untouched():
    fields = {"contract_no": {"value": None, "src": ""}}
    apply_rule_guards(fields)
    assert fields["contract_no"] == {"value": None, "src": ""}
