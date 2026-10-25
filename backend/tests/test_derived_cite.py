# 「以合同为准」的派生值沿用合同字段的出处（#113）：src 开头带《合同文件名》第N页、带页码；数据为虚构
from app.services.derived_fields import defendant_name_field, plaintiff_name_field

_CONTRACT = "虚构花园电梯安装合同.pdf"


def _fields(**extra):
    base = {
        "contract_type": {"value": "安装合同", "src": "《虚构审批表.pdf》合同类型"},
        "plaintiff_branch_raw": {"value": "集团/营销网络/梧桐分公司", "src": "《虚构审批表.pdf》合同分公司"},
        "defendant_name": {"value": "虚构置业有限公司", "src": "《虚构审批表.pdf》合同买方名称"},
        "contract_party_a": {"value": "虚构置业有限公司", "src": f"《{_CONTRACT}》第5页盖章页甲方", "page": 5},
        "contract_party_b": {
            "value": "日立电梯（中国）有限公司梧桐分公司",
            "src": f"《{_CONTRACT}》第5页盖章页乙方（OCR识别，请核实）",
            "page": 5,
        },
    }
    base.update(extra)
    return base


def test_defendant_name_cites_contract_file_and_page():
    r = defendant_name_field(_fields())
    assert r["src"].startswith(f"《{_CONTRACT}》第5页；按律师规则：以合同盖章页甲方")
    assert r["page"] == 5


def test_plaintiff_name_cites_contract_file_and_keeps_ocr_note():
    r = plaintiff_name_field(_fields())
    assert r["src"].startswith(f"《{_CONTRACT}》第5页；按律师规则：以合同盖章页乙方为准")
    assert "OCR识别，请核实" in r["src"] and r["page"] == 5


def test_mismatch_note_kept_after_cite():
    f = _fields(defendant_name={"value": "虚构置业有限公司（一期）", "src": "《虚构审批表.pdf》合同买方名称"})
    r = defendant_name_field(f)
    assert r["src"].startswith(f"《{_CONTRACT}》第5页；") and "括号内为备注" in r["src"]


def test_no_filename_upstream_no_prefix():
    f = _fields(contract_party_a={"value": "虚构置业有限公司", "src": "盖章页甲方"})
    r = defendant_name_field(f)
    assert r["src"].startswith("按律师规则：") and "page" not in r
