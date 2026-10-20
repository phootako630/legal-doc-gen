# 战略 / 框架合同：主合同争议条款只转引战略合同时，去战略合同里找；战略合同不提供本案字段。虚构数据。
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent import nodes as nodes_mod
from app.services import file_store
from app.services.extraction import apply_strategic_guards, refers_to_strategic

_REFERRAL = "本合同的货款结算和支付、违约责任、争议的解决、解除合同与其他约定按“战略合同”内相关条款执行。"
_STRATEGIC_TEXT = "电梯采购战略合同\n第三条 争议解决：因本合同引起的争议，提交南京仲裁委员会仲裁。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_REFERRAL, True),
        ("争议解决方式按双方签订的框架协议执行", True),
        ("争议提交南京仲裁委员会仲裁；其他事项按战略合同执行", False),  # 本身写了仲裁机构
        ("向工程所在地人民法院提起诉讼", False),
        (None, False),
    ],
)
def test_refers_to_strategic(text, expected):
    assert refers_to_strategic(text) is expected


_FILES = [
    {"filename": "主合同.pdf", "identified_type": "合同"},
    {"filename": "集团战略合同.pdf", "identified_type": "战略合同"},
]


def test_guard_clears_case_fields_taken_from_strategic_contract():
    fields = {
        "contract_party_a": {"value": "虚构集团有限公司", "src": "《集团战略合同.pdf》盖章页"},
        "contract_sign_date": {"value": "2020年1月1日", "src": "《集团战略合同.pdf》"},
        "contract_title": {"value": "某项目电梯买卖合同", "src": "《主合同.pdf》"},
        "dispute_clause_location": {"value": "第三条", "src": "《集团战略合同.pdf》"},
        "dispute_clause_text": {"value": "提交南京仲裁委员会仲裁", "src": "《集团战略合同.pdf》第三条"},
    }
    apply_strategic_guards(fields, _FILES)
    assert fields["contract_party_a"]["value"] is None
    assert fields["contract_sign_date"]["value"] is None
    assert fields["contract_title"]["value"] == "某项目电梯买卖合同"
    # 争议条款可以取自战略合同，但位置要写明是战略合同的条款
    assert fields["dispute_clause_location"]["value"] == "战略合同第三条"
    assert fields["dispute_clause_text"]["value"] == "提交南京仲裁委员会仲裁"


def test_guard_turns_referral_into_missing_and_keeps_main_location():
    fields = {
        "dispute_clause_location": {"value": "第十条", "src": "《主合同.pdf》"},
        "dispute_clause_text": {"value": _REFERRAL, "src": "《主合同.pdf》第十条"},
    }
    apply_strategic_guards(fields, _FILES)
    assert fields["dispute_clause_text"]["value"] is None
    assert "按战略 / 框架合同执行" in fields["dispute_clause_text"]["src"]
    assert fields["dispute_clause_location"]["value"] == "第十条"


def test_guard_noop_without_strategic_file():
    fields = {"dispute_clause_text": {"value": _REFERRAL, "src": "《主合同.pdf》"}}
    apply_strategic_guards(fields, [{"filename": "主合同.pdf", "identified_type": "合同"}])
    assert fields["dispute_clause_text"]["value"] == _REFERRAL  # 没传战略合同：保留原文给律师看


def _case_fields():
    return {
        "contract_sign_date": {"value": "2024年2月26日", "src": "《主合同.pdf》盖章页"},
        "contract_party_a": {"value": "虚构置业有限公司", "src": "《主合同.pdf》盖章页"},
        "contract_party_b": {"value": "某电梯公司江苏分公司", "src": "《主合同.pdf》盖章页"},
        "payment_clause_text": {"value": "验收后付清", "src": "《主合同.pdf》第七条"},
        "breach_interest_rate_text": {"value": "每日万分之五", "src": "《主合同.pdf》第九条"},
        "dispute_clause_location": {"value": "第十条", "src": "《主合同.pdf》"},
        "dispute_clause_text": {"value": None, "src": "主合同约定争议解决按战略 / 框架合同执行"},
    }


_STRATEGIC_RESULT = {
    "contract_party_a": {"value": "虚构集团有限公司", "src": "《集团战略合同.pdf》"},
    "dispute_clause_location": {"value": "第三条", "src": "《集团战略合同.pdf》"},
    "dispute_clause_text": {"value": "提交南京仲裁委员会仲裁", "src": "《集团战略合同.pdf》第三条"},
    "arbitration_institution": {"value": "南京仲裁委员会", "src": "《集团战略合同.pdf》第三条"},
}


def test_text_strategic_contract_supplies_dispute_clause(monkeypatch):
    chat = AsyncMock(return_value=_STRATEGIC_RESULT)
    monkeypatch.setattr(nodes_mod, "chat", chat)
    strategic = {
        "filename": "集团战略合同.pdf",
        "identified_type": "战略合同",
        "is_scanned": False,
        "text": _STRATEGIC_TEXT,
        "pages": [{"page": 1, "text": _STRATEGIC_TEXT}],
        "file_id": None,
    }
    state = {"extracted_fields": _case_fields(), "files": [strategic]}
    f = asyncio.run(nodes_mod.ocr_augment_node(state))["extracted_fields"]

    assert chat.await_count == 1  # 文字版：不 OCR，只定向抽取一次
    assert f["dispute_clause_text"]["value"] == "提交南京仲裁委员会仲裁"
    assert f["dispute_clause_location"]["value"] == "第十条及战略合同第三条"
    assert "OCR" not in f["dispute_clause_text"]["src"]
    assert f["arbitration_institution"]["value"] == "南京仲裁委员会"
    assert f["contract_party_a"]["value"] == "虚构置业有限公司"  # 本案当事人不变


def test_scanned_main_referral_then_scanned_strategic(monkeypatch):
    main_result = {
        "dispute_clause_location": {"value": "第十条", "src": "《主合同.pdf》"},
        "dispute_clause_text": {"value": _REFERRAL, "src": "《主合同.pdf》第十条"},
    }
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[main_result, _STRATEGIC_RESULT]))

    async def fake_ocr(_pdf, n):
        return "第十条 争议的解决 " + _REFERRAL if n == 1 else "第八条 其他约定"

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake_ocr))

    def scanned(name, dtype):
        return {
            "filename": name,
            "identified_type": dtype,
            "is_scanned": True,
            "text": "",
            "pages": [],
            "page_count": 2,
            "file_id": file_store.put(b"%PDF-fake"),
        }

    fields = _case_fields()
    fields.pop("dispute_clause_location")
    fields["dispute_clause_text"] = {"value": None, "src": ""}
    state = {"extracted_fields": fields, "files": [scanned("集团战略合同.pdf", "战略合同"), scanned("主合同.pdf", "合同")]}
    f = asyncio.run(nodes_mod.ocr_augment_node(state))["extracted_fields"]

    assert f["dispute_clause_text"]["value"] == "提交南京仲裁委员会仲裁"
    assert f["dispute_clause_location"]["value"] == "第十条及战略合同第三条"
    assert "OCR" in f["dispute_clause_text"]["src"] and "OCR" in f["dispute_clause_location"]["src"]


def test_guard_rechecks_after_provenance_finds_strategic_source(monkeypatch):
    # 模型把战略合同里的日期标成主合同来源；锚定改成实际命中的战略合同后，仍须清空（#56 评审）
    main_text = "电梯买卖合同\n甲方：虚构置业有限公司\n第十条 争议解决按战略合同执行。"
    strategic_text = "电梯采购战略合同\n签订日期：2020年1月1日\n第三条 争议提交南京仲裁委员会仲裁。"
    files = [
        {"filename": "主合同.pdf", "identified_type": "合同", "is_scanned": False, "text": main_text,
         "pages": [{"page": 1, "text": main_text}], "file_id": None},
        {"filename": "战略合同.pdf", "identified_type": "战略合同", "is_scanned": False, "text": strategic_text,
         "pages": [{"page": 1, "text": strategic_text}], "file_id": None},
    ]
    llm_fields = {"contract_sign_date": {"value": "2020年1月1日", "src": "《主合同.pdf》"}}
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=llm_fields))
    out = asyncio.run(nodes_mod.extract_node({"files": files, "checklist": {}}))
    assert out["extracted_fields"]["contract_sign_date"]["value"] is None


def test_unknown_main_location_cites_strategic_only(monkeypatch):
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_STRATEGIC_RESULT))
    strategic = {
        "filename": "集团战略合同.pdf",
        "identified_type": "战略合同",
        "is_scanned": False,
        "text": _STRATEGIC_TEXT,
        "pages": [{"page": 1, "text": _STRATEGIC_TEXT}],
        "file_id": None,
    }
    fields = _case_fields()
    fields.pop("dispute_clause_location")  # 主合同的转引条款号没读到
    f = asyncio.run(nodes_mod.ocr_augment_node({"extracted_fields": fields, "files": [strategic]}))["extracted_fields"]
    assert f["dispute_clause_location"]["value"] == "战略合同第三条"
