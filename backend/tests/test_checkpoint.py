# checkpoint 持久化测试：重启后仍可 resume；过期会话被清除。
import asyncio
import time
from unittest.mock import AsyncMock

from app.agent import checkpoint
from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from app.agent.runner import run_analyze, run_resume

_OK = {
    "has_approval": True,
    "has_contract": True,
    "has_acceptance": True,
    "can_proceed": True,
    "missing": [],
    "notes": "",
}
_CONFLICT = {
    "total_amount": {"value": 900000, "src": "《审批表》"},
    "paid_amount": {"value": 638667.2, "src": "《审批表》"},
    "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
    "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
}


def test_resume_survives_restart(monkeypatch):
    monkeypatch.setattr(
        nodes_mod,
        "chat",
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
        nodes_mod,
        "chat",
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


def test_db_file_is_owner_only_and_existing_loose_file_tightened(tmp_path, monkeypatch):
    import os

    from app import config

    db = tmp_path / "loose.sqlite"
    db.write_bytes(b"")
    os.chmod(db, 0o644)  # 模拟旧版本留下的宽松权限文件
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db))

    asyncio.run(checkpoint.get_saver())
    assert (os.stat(db).st_mode & 0o777) == 0o600
    # WAL 边车文件沿用主库权限
    for suffix in ("-wal", "-shm"):
        side = str(db) + suffix
        if os.path.exists(side):
            assert (os.stat(side).st_mode & 0o777) == 0o600


def test_resume_refuses_expired_run_without_any_cleanup_having_run(monkeypatch):
    # 过期保证不能依赖清理任务恰好跑过：resume 本身就要拒绝
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[_OK, _CONFLICT, "说明\n---HIGHLIGHT---\n- 1"]),
    )

    async def scenario():
        state = await run_analyze([{"filename": "审批表.pdf", "text": "x"}], True)
        await checkpoint._conn.execute(
            "UPDATE run_meta SET created_at = ? WHERE thread_id = ?",
            (time.time() - 30 * 86400, state.run_id),
        )
        await checkpoint._conn.commit()
        try:
            await run_resume(state.run_id, {"total_amount": 894934})
        except KeyError:
            pass
        else:
            raise AssertionError("过期会话不应能 resume")
        return state.run_id

    run_id = asyncio.run(scenario())

    # 且数据已被当场清除（再查登记表已无此会话）
    async def remaining():
        saver = await checkpoint.get_saver()
        assert saver is not None
        async with checkpoint._conn.execute(
            "SELECT COUNT(*) FROM run_meta WHERE thread_id = ?", (run_id,)
        ) as cur:
            return (await cur.fetchone())[0]

    assert asyncio.run(remaining()) == 0


def test_startup_lifespan_purges_expired_data_without_new_traffic():
    # 服务启动即清理一次、之后定时清理——不需要有新案件进来
    import os

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import file_store

    fid = file_store.put(b"expired-contract")
    path = os.path.join(file_store.UPLOAD_DIR, fid)
    long_ago = time.time() - 30 * 86400
    os.utime(path, (long_ago, long_ago))

    with TestClient(app) as client:  # 进入即触发 lifespan
        client.get("/")
        # 清理循环在后台任务里立即执行；给事件循环一点时间
        for _ in range(50):
            if not os.path.exists(path):
                break
            time.sleep(0.05)
    assert not os.path.exists(path)
