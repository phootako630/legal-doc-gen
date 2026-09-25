# 按需 OCR 节点单测：缺条款 + 扫描合同 → 按批 OCR、找到条款正文即停、回填条款 + 真实页码。
#
# mock 掉 nodes.ocr_page（不触网）与 nodes.chat（不消耗配额）。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store

# 第 2 页含三类条款正文与可锚定的条款原文
_PAGE1 = "安装工程合同 封面 甲方乙方"
_PAGE2 = (
    "第五条 付款：验收合格后30日内支付合同总价的100%；"
    "第六条 违约：逾期付款按每日万分之五支付违约金；"
    "第七条 争议：提交南京仲裁委员会仲裁。"
)
_FILLER = "第八条 其他约定"
# 目录页：章名里有付款/违约/争议字样，但不是条款正文，不应据此停止
_TOC = "目 录 第十三章 工程款的核实与支付 第二十章 争议、违约及索赔 第二十一章 合同生效"


def _ocr_by_page(pages: dict[int, str]):
    """按页码返回 OCR 文本的假 ocr_page（未列出的页返回填充文本）。"""

    async def fake(_pdf, n):
        return pages.get(n, _FILLER)

    return AsyncMock(side_effect=fake)


_CLAUSE_RESULT = {
    "payment_clause_location": {"value": "第五条", "src": "《安装合同.pdf》"},
    "payment_clause_text": {
        "value": "验收合格后30日内支付合同总价的100%",
        "src": "《安装合同.pdf》",
    },
    "breach_interest_clause_location": {"value": "第六条", "src": "《安装合同.pdf》"},
    "breach_interest_rate_text": {
        "value": "逾期付款按每日万分之五支付违约金",
        "src": "《安装合同.pdf》",
    },
    "dispute_clause_location": {"value": "第七条", "src": "《安装合同.pdf》"},
    "dispute_clause_text": {
        "value": "提交南京仲裁委员会仲裁",
        "src": "《安装合同.pdf》",
    },
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
    ocr = _ocr_by_page({1: _PAGE1, 2: _PAGE2})  # 第 2 页有条款正文 → 第一批后即停
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
            _scanned_contract(page_count=12),
        ],
    }
    out = asyncio.run(nodes_mod.ocr_augment_node(state))

    f = out["extracted_fields"]
    assert f["payment_clause_text"]["value"] == "验收合格后30日内支付合同总价的100%"
    # 命中即停：第一批（5 页）内找到条款正文，不再 OCR 后续批次（共 12 页）
    assert ocr.await_count == 5
    # 条款字段拿到真实页码 + OCR 通道（逐页锚定所得）
    assert f["payment_clause_text"]["page"] == 2
    assert f["payment_clause_text"]["channel"] == "ocr"
    # src 带 OCR 标记：审核页判「待核实」、起诉状标「⚠️ 待核实」都靠它
    src = f["payment_clause_text"]["src"]
    assert src.startswith("《安装合同.pdf》")
    assert "OCR" in src


def test_augment_marks_ocr_even_when_llm_omits_src(monkeypatch):
    # LLM 没按要求写 src（空串）→ 仍补上《文件名》+ OCR 标记，不能被当成「正常」
    no_src = {k: {"value": v["value"], "src": ""} for k, v in _CLAUSE_RESULT.items()}
    monkeypatch.setattr(nodes_mod, "ocr_page", _ocr_by_page({1: _PAGE1, 2: _PAGE2}))
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=no_src))
    state = {
        "extracted_fields": _missing_clause_fields(),
        "files": [_scanned_contract()],
    }
    f = asyncio.run(nodes_mod.ocr_augment_node(state))["extracted_fields"]
    for key in (
        "payment_clause_text",
        "breach_interest_rate_text",
        "dispute_clause_text",
    ):
        assert f[key]["src"] == "《安装合同.pdf》（OCR识别，请核实）"
    # 已有来源（审批表）的非条款字段不受影响
    assert "OCR" not in f["defendant_name"]["src"]


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


def _run_augment(monkeypatch, ocr, chat_result=_CLAUSE_RESULT, fields=None, **contract):
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=chat_result))
    state = {
        "extracted_fields": fields or _missing_clause_fields(),
        "files": [_scanned_contract(**contract)],
    }
    return asyncio.run(nodes_mod.ocr_augment_node(state))


def test_toc_page_does_not_stop_ocr(monkeypatch):
    # 目录页（第 2 页）章名含付款/违约/争议，旧逻辑在此即停；现应继续翻到正文（第 7 页）
    ocr = _ocr_by_page({2: _TOC, 7: _PAGE2})
    _run_augment(monkeypatch, ocr, page_count=20)
    assert ocr.await_count == 10  # 第 1–5 批无正文 → 第 6–10 批找到 → 停


def test_single_page_timeout_is_skipped(monkeypatch):
    # 单页超时（实测密集表格页）不应中断整份合同的 OCR
    async def fake(_pdf, n):
        if n == 1:
            raise TimeoutError("第 1 页 OCR 超时")
        return _PAGE2 if n == 2 else _FILLER

    out = _run_augment(monkeypatch, AsyncMock(side_effect=fake), page_count=5)
    f = out["extracted_fields"]
    assert f["payment_clause_text"]["page"] == 2


def test_missing_page_count_falls_back_to_pdf(monkeypatch):
    # 调用方没带 page_count（旧客户端/API 模型漏字段）→ 从 PDF 字节读页数，不能 0 页了事
    monkeypatch.setattr(nodes_mod, "pdf_page_count", lambda _pdf: 3)
    ocr = _ocr_by_page({2: _PAGE2})
    _run_augment(monkeypatch, ocr, page_count=0)
    assert ocr.await_count == 3


def test_contract_fields_override_guesses(monkeypatch):
    # 合同名称/合同台数/安装地址以合同为准，覆盖此前从审批表等推测的值
    fields = _missing_clause_fields()
    fields["contract_title"] = {"value": "电梯安装合同", "src": "《审批表.pdf》"}
    result = dict(_CLAUSE_RESULT)
    result["contract_title"] = {
        "value": "某某二期电梯安装工程合同",
        "src": "《安装合同.pdf》封面",
    }
    result["elevator_qty_by_contract"] = {"value": 14, "src": "《安装合同.pdf》协议书"}
    ocr = _ocr_by_page({1: "某某二期电梯安装工程合同", 2: _PAGE2})
    out = _run_augment(monkeypatch, ocr, chat_result=result, fields=fields)
    f = out["extracted_fields"]
    assert f["contract_title"]["value"] == "某某二期电梯安装工程合同"
    assert "OCR" in f["contract_title"]["src"]
    assert f["elevator_qty_by_contract"]["value"] == 14
