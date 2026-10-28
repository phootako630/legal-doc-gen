# 合同编号把关（#125）：审批表标题紧挨合同号栏时，模型会把「AH……-项目名」整段当合同号。
# 只保留「字母 + 数字」的编号本身（位数不限），原抽取串留在 raw_value、出处写明删了什么；
# 不是「字母 + 数字」格式又切不出编号的原样保留、标待核实，不猜。虚构编号。
from app.services.extraction import apply_rule_guards

_SRC = "《虚构诉讼审批流程.pdf》合同号栏"


def _guard(value, src=_SRC):
    fields = {"contract_no": {"value": value, "src": src}}
    apply_rule_guards(fields)
    return fields["contract_no"]


def test_trailing_project_name_is_trimmed_and_original_kept():
    node = _guard("AH9000001-虚构南翔路项目")
    assert node["value"] == "AH9000001"
    assert node["raw_value"] == "AH9000001-虚构南翔路项目"
    assert "原抽取「AH9000001-虚构南翔路项目」" in node["src"] and "待核实" not in node["src"]
    # 全角连字符、空格分隔同样处理
    assert _guard("AH9000001－虚构路项目")["value"] == "AH9000001"
    assert _guard("AH9000001 虚构路项目")["value"] == "AH9000001"


def test_digit_and_letter_count_not_fixed():
    # 位数不写死（#129 评审）：短编号、长字母前缀都照样截取
    assert _guard("AH123-虚构项目")["value"] == "AH123"
    assert _guard("XGHTABCDEF77-虚构项目")["value"] == "XGHTABCDEF77"


def test_plain_number_unchanged():
    for value in ("AH9000001", "AH123", "XG20990731"):
        assert _guard(value) == {"value": value, "src": _SRC}


def test_other_formats_kept_and_flagged():
    # 不是「字母 + 数字」：含中文、分段编号、纯数字一律原样保留并标待核实（#129 评审）
    for value in ("虚构项目合同第三号", "XG-HT-2099-0731", "20990731"):
        node = _guard(value)
        assert node["value"] == value
        assert "待核实" in node["src"]
        assert "raw_value" not in node


def test_guard_is_idempotent():
    # 抽取后与按需 OCR 后各跑一次：结果不变，说明不重复追加
    for value in ("AH9000001-虚构路项目", "XG-HT-2099-0731"):
        fields = {"contract_no": {"value": value, "src": _SRC}}
        apply_rule_guards(fields)
        once = dict(fields["contract_no"])
        apply_rule_guards(fields)
        assert fields["contract_no"] == once


def test_missing_contract_no_untouched():
    fields = {"contract_no": {"value": None, "src": ""}}
    apply_rule_guards(fields)
    assert fields["contract_no"] == {"value": None, "src": ""}
