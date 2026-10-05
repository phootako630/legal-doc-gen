# checkpoint 持久化测试：重启后仍可 resume；过期会话被清除。
import asyncio
import time
from unittest.mock import AsyncMock

from app.agent import checkpoint
from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from app.agent.runner import run_analyze, run_resume

_OK = {"has_approval": True, "has_contract": True, "has_acceptance": True,
       "can_proceed": True, "missing": [], "notes": ""}
_CONFLICT = {
    "total_amount": {"value": 900000, "src": "《审批表》"},
    "paid_amount": {"value": 638667.2, "src": "《审批表》"},
    "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
    "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
}


def test_resume_survives_restart(monkeypatch):
    monkeypatch.setattr(
        nodes_mod, "chat",
        AsyncMock(side_effect=[_OK, _CONFLICT, "说明\n---HIGHLIGHT---\n- 1"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is not None

    # 模拟服务重启：丢弃图与连接，下次调用从磁盘重新打开
    asyncio.run(reset_graph())

    resumed = asyncio.run(run_resume(state.run_id, {"total_amount": 894934}))
    assert resumed.pending is None
    assert resumed.run_id == state.run_id


def test_resume_unknown_run_raises_keyerror():
    try:
        asyncio.run(run_resume("0" * 32, {}))
    except KeyError:
        return
    raise AssertionError("未知 run_id 应抛 KeyError")


def test_purge_expired_removes_old_runs(monkeypatch):
    monkeypatch.setattr(
        nodes_mod, "chat",
        AsyncMock(side_effect=[_OK, _CONFLICT, "说明\n---HIGHLIGHT---\n- 1"]),
    )

    async def scenario():
        state = await run_analyze([{"filename": "审批表.pdf", "text": "x"}], True)
        # 把该会话的登记时间改到 8 天前，再清理（保留期 7 天）
        await checkpoint._conn.execute(
            "UPDATE run_meta SET created_at = ? WHERE thread_id = ?",
            (time.time() - 8 * 86400, state.run_id),
        )
        await checkpoint._conn.commit()
        removed = await checkpoint.purge_expired(retention_days=7)
        return state.run_id, removed

    run_id, removed = asyncio.run(scenario())
    assert removed == 1
    # 清除后 resume 视为会话不存在
    try:
        asyncio.run(run_resume(run_id, {}))
    except KeyError:
        return
    raise AssertionError("过期会话应已被清除")
