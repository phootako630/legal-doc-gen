# 按需 OCR 节点单测：缺条款 + 扫描合同 → 逐页 OCR 命中即停、回填条款 + 真实页码。
#
# mock 掉 nodes.ocr_page（不触网）与 nodes.chat（不消耗配额）。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store

# 第 2 页含三类条款关键词与可锚定的条款原文
_PAGE1 = "安装工程合同 封面 甲方乙方"
_PAGE2 = (
    "第五条 付款：验收合格后30日内支付货款；"
    "第六条 违约：逾期付款按每日万分之五计算；"
    "第七条 争议：提交仲裁委员会仲裁。"
)

_CLAUSE_RESULT = {
    "payment_clause_location": {"value": "第五条", "src": "《安装合同.pdf》"},
    "payment_clause_text": {
        "value": "验收合格后30日内支付货款",
        "src": "《安装合同.pdf》",
    },
    "breach_interest_clause_location": {"value": "第六条", "src": "《安装合同.pdf》"},
    "breach_interest_rate_text": {
        "value": "逾期付款按每日万分之五计算",
        "src": "《安装合同.pdf》",
    },
    "dispute_clause_location": {"value": "第七条", "src": "《安装合同.pdf》"},
    "dispute_clause_text": {"value": "提交仲裁委员会仲裁", "src": "《安装合同.pdf》"},
}


def _missing_clause_fields():
    return {
        "defendant_name": {"value": "某电梯公司", "src": "《审批表.pdf》"},
        "payment_clause_text": {"value": None, "src": ""},
        "breach_interest_rate_text": {"value": None, "src": ""},
        "dispute_clause_text": {"value": None, "src": ""},
    }


def _scanned_contract(page_count=3):
    fid = file_store.put(b"%PDF-fake-bytes")
    return {
        "filename": "安装合同.pdf",
        "identified_type": "合同",
        "is_scanned": True,
        "text": "",
        "pages": [],
        "page_count": page_count,
        "file_id": fid,
    }


def test_augment_ocrs_fills_clauses_with_page(monkeypatch):
    ocr = AsyncMock(side_effect=[_PAGE1, _PAGE2])  # 第1页无条款，第2页命中三条款→停
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSE_RESULT))

    state = {
        "extracted_fields": _missing_clause_fields(),
        "files": [
            {
                "filename": "审批表.pdf",
                "identified_type": "审批表",
                "is_scanned": False,
                "text": "某电梯公司",
                "pages": [{"page": 1, "text": "某电梯公司"}],
                "file_id": None,
            },
            _scanned_contract(),
        ],
    }
    out = asyncio.run(nodes_mod.ocr_augment_node(state))

    f = out["extracted_fields"]
    assert f["payment_clause_text"]["value"] == "验收合格后30日内支付货款"
    # 命中即停：只 OCR 到第 2 页（共 3 页）
    assert ocr.await_count == 2
    # 条款字段拿到真实页码 + OCR 通道（逐页锚定所得）
    assert f["payment_clause_text"]["page"] == 2
    assert f["payment_clause_text"]["channel"] == "ocr"


def test_no_scanned_contract_is_noop(monkeypatch):
    ocr = AsyncMock()
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock())
    state = {
        "extracted_fields": _missing_clause_fields(),
        "files": [
            {
                "filename": "审批表.pdf",
                "identified_type": "审批表",
                "is_scanned": False,
                "text": "x",
                "pages": [],
                "file_id": None,
            },
        ],
    }
    assert asyncio.run(nodes_mod.ocr_augment_node(state)) == {}
    ocr.assert_not_awaited()


def test_clauses_present_skips_ocr(monkeypatch):
    ocr = AsyncMock()
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock())
    fields = {
        "payment_clause_text": {"value": "已有付款条款", "src": "x"},
        "breach_interest_rate_text": {"value": "已有违约", "src": "x"},
        "dispute_clause_text": {"value": "已有争议", "src": "x"},
    }
    state = {"extracted_fields": fields, "files": [_scanned_contract()]}
    assert asyncio.run(nodes_mod.ocr_augment_node(state)) == {}
    ocr.assert_not_awaited()


def test_ocr_failure_degrades_gracefully(monkeypatch):
    # OCR 不可用（如未配置 key）→ 抛 RuntimeError → 优雅降级、条款保持缺失
    monkeypatch.setattr(
        nodes_mod, "ocr_page", AsyncMock(side_effect=RuntimeError("no key"))
    )
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock())
    state = {
        "extracted_fields": _missing_clause_fields(),
        "files": [_scanned_contract()],
    }
    assert asyncio.run(nodes_mod.ocr_augment_node(state)) == {}
