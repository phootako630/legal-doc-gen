# 全案 OCR 总上限（#71）：审批表、合同、验收报告、补读设备清单共用 AGENT_MAX_TOTAL_OCR_PAGES；
# 上限用完仍没读到的条款在出处与起诉状里写明原因。OCR 与 LLM 一律打桩，材料为虚构数据。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store
from app.services.complaint_renderer import OCR_BUDGET_HINT, render_complaint


def _scanned(filename: str, kind: str, pages: int) -> dict:
    return {
        "filename": filename,
        "identified_type": kind,
        "is_scanned": True,
        "page_count": pages,
        "file_id": file_store.put(f"%PDF-{filename}".encode()),
        "text": "",
    }


def _run_ocr_nodes(state: dict) -> dict:
    """按图中的顺序跑四个 OCR 节点，状态逐个合并（与 LangGraph 的覆盖语义一致）。"""
    for node in (
        nodes_mod.approval_ocr_node,
        nodes_mod.ocr_augment_node,
        nodes_mod.report_ocr_node,
        nodes_mod.contract_scan_node,
    ):
        state = {**state, **asyncio.run(node(state))}
    return state


def test_total_ocr_pages_capped_across_nodes(monkeypatch):
    calls: list[int] = []

    async def fake_ocr(_pdf, n):
        calls.append(n)
        return f"第{n}页扫描正文，与条款无关的内容"

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake_ocr))
    monkeypatch.setattr(nodes_mod, "_extract_clauses", AsyncMock(return_value={}))
    monkeypatch.setattr(nodes_mod, "AGENT_MAX_TOTAL_OCR_PAGES", 25)
    state = {
        "files": [
            _scanned("审批表.pdf", "审批表", 6),
            _scanned("安装合同.pdf", "合同", 40),
            _scanned("监检报告.pdf", "验收报告", 20),
        ],
        # 合同与报告台数不同：contract_scan 也想补读合同剩余页
        "extracted_fields": {
            "elevator_qty_by_contract": {"value": "6", "src": "《安装合同.pdf》"},
            "elevator_qty_by_acceptance": {"value": "4", "src": "《监检报告.pdf》"},
        },
    }
    out = _run_ocr_nodes(state)

    # 各子上限相加远超 25（审批表 6 + 合同 40 + 报告 20），总数仍被卡在 25
    assert len(calls) == 25
    assert out["ocr_pages_used"] == 25
    # 审批表在最前面，先读满
    assert calls[:6] == [1, 2, 3, 4, 5, 6]


def test_budget_hit_marks_missing_clauses(monkeypatch):
    monkeypatch.setattr(
        nodes_mod, "ocr_page", AsyncMock(return_value="目录与通用条款，无付款约定")
    )
    monkeypatch.setattr(nodes_mod, "_extract_clauses", AsyncMock(return_value={}))
    monkeypatch.setattr(nodes_mod, "AGENT_MAX_TOTAL_OCR_PAGES", 10)
    state = {
        "files": [_scanned("安装合同.pdf", "合同", 40)],
        "extracted_fields": {"total_amount": {"value": 1000, "src": "《审批表》"}},
    }
    out = asyncio.run(nodes_mod.ocr_augment_node(state))

    assert out["ocr_pages_used"] == 10
    for key in ("payment_clause_text", "dispute_clause_text"):
        node = out["extracted_fields"][key]
        assert node["value"] is None and OCR_BUDGET_HINT in node["src"]
    # 起诉状：【待补充】后写明原因，不当作「合同没约定」
    text = render_complaint(out["extracted_fields"])
    assert f"【待补充】（{OCR_BUDGET_HINT}，请查看合同原件）" in text


def test_budget_already_used_up_still_marks(monkeypatch):
    # 前面的节点已用满总上限：一页都不读，但仍提示「可能未读到」
    ocr = AsyncMock(return_value="不应调用")
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "AGENT_MAX_TOTAL_OCR_PAGES", 10)
    state = {
        "files": [_scanned("安装合同.pdf", "合同", 40)],
        "extracted_fields": {},
        "ocr_pages_used": 10,
    }
    out = asyncio.run(nodes_mod.ocr_augment_node(state))
    ocr.assert_not_called()
    assert OCR_BUDGET_HINT in out["extracted_fields"]["payment_clause_text"]["src"]


def test_contract_fully_read_is_not_marked(monkeypatch):
    # 合同页数在上限内且全部读完：没找到就是没找到，不加「上限」提示
    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(return_value="无关正文"))
    monkeypatch.setattr(nodes_mod, "_extract_clauses", AsyncMock(return_value={}))
    state = {"files": [_scanned("安装合同.pdf", "合同", 6)], "extracted_fields": {}}
    out = asyncio.run(nodes_mod.ocr_augment_node(state))
    assert out["ocr_pages_used"] == 6
    src = str(out["extracted_fields"].get("payment_clause_text", {}).get("src") or "")
    assert OCR_BUDGET_HINT not in src
