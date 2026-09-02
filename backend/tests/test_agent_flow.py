# agent 流程测试：analyze → (冲突) interrupt → resume → pending=null；缺材料 → pending=missing
#
# mock 掉 nodes.chat，不触网、不消耗配额。用 asyncio.run 驱动 async runner。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume

_OK_CHECKLIST = {
    "has_approval": True,
    "has_contract": True,
    "has_acceptance": True,
    "can_proceed": True,
    "missing": [],
    "notes": "",
}


def _patch_chat(monkeypatch, side_effect):
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=side_effect))


def _conflict_fields():
    # 金额不勾稽：900000 ≠ 638667.2 + 256266.8 → 校验冲突
    return {
        "total_amount": {"value": 900000, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
    }


def test_conflict_triggers_interrupt_then_resume_clears(monkeypatch):
    _patch_chat(
        monkeypatch,
        [
            _OK_CHECKLIST,  # 清点
            _conflict_fields(),  # 抽取
            "校验说明\n---HIGHLIGHT---\n- 项1",  # resume 后的 validate prose
        ],
    )

    # analyze：应在校验节点冲突处 interrupt
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is not None
    assert state.pending.kind == "conflict"
    assert "total_amount" in state.pending.field_keys
    # 断点处已带确定性校验结论
    assert any(v["key"] == "amount_reconcile" and v["is_conflict"] for v in state.validations)

    # resume：律师把总价改成能勾稽的值 → 冲突消除，pending 清空
    resumed = asyncio.run(run_resume(state.run_id, {"total_amount": 894934}))
    assert resumed.run_id == state.run_id
    assert resumed.pending is None
    assert not any(v["is_conflict"] for v in resumed.validations)
    assert resumed.validation_report == "校验说明"
    assert resumed.readiness > 0


def test_missing_materials_ends_with_pending(monkeypatch):
    _patch_chat(
        monkeypatch,
        [{"can_proceed": False, "missing": ["验收报告"], "notes": "缺少监检报告"}],
    )
    state = asyncio.run(run_analyze([{"filename": "合同.pdf", "text": "x"}], True))
    assert state.pending is not None
    assert state.pending.kind == "missing"
    assert "验收报告" in state.pending.question


def test_no_conflict_completes_without_interrupt(monkeypatch):
    consistent = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
        "defendant_name": {"value": "某被告", "src": "《审批表》"},
    }
    _patch_chat(
        monkeypatch,
        [_OK_CHECKLIST, consistent, "无冲突\n---HIGHLIGHT---\n"],
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is None
    assert not any(v["is_conflict"] for v in state.validations)
    assert state.validation_report == "无冲突"


def test_resume_unknown_run_id_raises(monkeypatch):
    _patch_chat(monkeypatch, [])
    try:
        asyncio.run(run_resume("nonexistent-run-id", {}))
    except KeyError:
        return
    raise AssertionError("未知 run_id 应抛 KeyError")
