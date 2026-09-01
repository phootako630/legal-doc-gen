# extract 流程集成测试：验证「校验移出 LLM」——代码跑 run_all_checks，
# LLM 仅据确定性结论措辞，且 validations 进入响应。
#
# mock 掉 chat 与 load_prompt，不触网、不消耗配额。用 asyncio.run 驱动。
import asyncio
from unittest.mock import AsyncMock

from app.routers import extract as extract_module
from app.routers.extract import ExtractRequest, FileInput, extract


def _patch(monkeypatch, chat_side_effect):
    """记录 load_prompt 收到的变量，替换 chat 为按序返回的 AsyncMock。"""
    seen: dict[str, dict] = {}

    def fake_load_prompt(name: str, variables: dict) -> str:
        seen[name] = variables
        return f"PROMPT::{name}"

    monkeypatch.setattr(extract_module, "load_prompt", fake_load_prompt)
    monkeypatch.setattr(extract_module, "chat", AsyncMock(side_effect=chat_side_effect))
    return seen


def test_code_validation_drives_conflict_and_llm_only_phrases(monkeypatch):
    # 金额不勾稽：900000 ≠ 638667.2 + 256266.8 → 代码应判冲突
    extracted = {
        "total_amount": {"value": 900000, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
    }
    seen = _patch(
        monkeypatch,
        [
            {"can_proceed": True},  # Step1 清点
            extracted,  # Step2 抽取
            "校验报告文本\n---HIGHLIGHT---\n- 高亮项1",  # Step3b 说明文本
        ],
    )

    req = ExtractRequest(files=[FileInput(filename="审批表.pdf", text="任意文本")])
    resp = asyncio.run(extract(req))

    # 1) 确定性校验结论进入响应，金额勾稽判冲突
    amount = next(v for v in resp.validations if v["key"] == "amount_reconcile")
    assert amount["is_conflict"] is True
    assert "勾稽" in amount["message"]

    # 2) 信息不足的检查为"不适用"，不算冲突
    credit = next(v for v in resp.validations if v["key"] == "credit_code")
    assert credit["applicable"] is False
    assert credit["is_conflict"] is False

    # 3) validate prompt 收到确定性结论、不再注入 material_checklist（LLM 不做判断）
    vvars = seen["prompt-a-validate.md"]
    assert "deterministic_checks" in vvars
    assert "冲突" in vvars["deterministic_checks"]
    assert "material_checklist" not in vvars

    # 4) 报告/高亮按分隔符切分
    assert resp.validation_report == "校验报告文本"
    assert resp.highlight_list == "- 高亮项1"


def test_all_consistent_yields_no_conflict(monkeypatch):
    extracted = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "elevator_qty_by_approval": {"value": 6, "src": "《审批表》"},
        "elevator_qty_by_contract": {"value": 6, "src": "《合同》"},
        "elevator_qty_by_acceptance": {"value": 6, "src": "《验收报告》"},
    }
    _patch(
        monkeypatch,
        [
            {"can_proceed": True},
            extracted,
            "无冲突\n---HIGHLIGHT---\n",
        ],
    )

    req = ExtractRequest(files=[FileInput(filename="审批表.pdf", text="任意文本")])
    resp = asyncio.run(extract(req))
    assert not any(v["is_conflict"] for v in resp.validations)
