# 按账号隔离案件：只有发起分析的账号能取回、继续、接回、反馈、统计改动；别人的会话按「不存在」处理。
# LLM 一律打桩，账号与材料均为虚构。
import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agent import checkpoint, runner
from app.agent import nodes as nodes_mod
from app.services import auth_store

_FILES = [{"filename": "审批表.pdf", "text": "虚构审批表"}]
_CHECKLIST = {"can_proceed": True, "missing": [], "notes": ""}
# 金额不勾稽 → 校验断点，会话停在待律师决定处，可以测 resume
_FIELDS = {
    "total_amount": {"value": 900000, "src": "《审批表》"},
    "paid_amount": {"value": 638667.2, "src": "《审批表》"},
    "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
}


def _patch_chat(monkeypatch, runs: int = 1):
    monkeypatch.setattr(
        nodes_mod, "chat", AsyncMock(side_effect=[_CHECKLIST, _FIELDS] * runs)
    )


def test_only_owner_can_read_resume_and_count_edits(monkeypatch):
    _patch_chat(monkeypatch)
    case = asyncio.run(runner.run_analyze(_FILES, True, owner="lawyer01"))
    assert case.pending is not None

    assert asyncio.run(runner.get_case(case.run_id, "lawyer02")) is None
    assert asyncio.run(runner.get_case(case.run_id, "lawyer01")).run_id == case.run_id
    assert (
        asyncio.run(runner.get_case(case.run_id, None)) is not None
    )  # 未开启登录不限制
    assert asyncio.run(runner.delivered_fields(case.run_id, "lawyer02")) is None
    with pytest.raises(KeyError):
        asyncio.run(
            runner.run_resume(case.run_id, {"total_amount": 894934}, "lawyer02")
        )


def test_legacy_run_without_owner_stays_accessible():
    # 升级前建的会话没有记录账号：不限制，随 7 天保留期自然消失
    asyncio.run(checkpoint.record_run("a" * 32))
    assert asyncio.run(checkpoint.can_access("a" * 32, "anyone"))


def test_result_by_key_only_for_owner(monkeypatch):
    _patch_chat(monkeypatch)
    first = asyncio.run(
        runner.run_analyze_once("key-owner-00001", _FILES, True, "lawyer01")
    )
    assert asyncio.run(runner.analyze_result("key-owner-00001", "lawyer02")) is None
    mine = asyncio.run(runner.analyze_result("key-owner-00001", "lawyer01"))
    assert mine.run_id == first.run_id
    # 别的账号拿同一编号再分析：按「编号对应别的请求」拒绝，接不上别人的结果
    with pytest.raises(runner.IdempotencyConflict):
        asyncio.run(
            runner.run_analyze_once("key-owner-00001", _FILES, True, "lawyer02")
        )


def test_result_of_running_analysis_only_for_owner(monkeypatch):
    gate = asyncio.Event()
    replies = iter([_CHECKLIST, _FIELDS])

    async def slow_chat(*_a, **_k):
        await gate.wait()
        return next(replies)

    monkeypatch.setattr(nodes_mod, "chat", slow_chat)

    async def scenario():
        task = asyncio.create_task(
            runner.run_analyze_once("key-owner-00002", _FILES, True, "lawyer01")
        )
        for _ in range(500):
            if "key-owner-00002" in runner._inflight:
                break
            await asyncio.sleep(0.01)
        other = await runner.analyze_result(
            "key-owner-00002", "lawyer02"
        )  # 不等，直接 None
        gate.set()
        await task
        return other

    assert asyncio.run(scenario()) is None


def test_http_other_account_gets_404(monkeypatch):
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    auth_store.create_user("lawyer01", "虚构的足够长密码123")
    auth_store.create_user("lawyer02", "虚构的足够长密码456")
    owner_cookie = {"session": auth_store.create_session("lawyer01")}
    other_cookie = {"session": auth_store.create_session("lawyer02")}
    _patch_chat(monkeypatch)
    run_id = asyncio.run(runner.run_analyze(_FILES, True, owner="lawyer01")).run_id

    from app.main import app

    client = TestClient(app)
    client.cookies.update(other_cookie)
    assert client.get(f"/api/case/{run_id}").status_code == 404
    assert client.post("/api/resume", json={"run_id": run_id}).status_code == 404
    fb = {"run_id": run_id, "category": "other"}
    assert client.post("/api/feedback", json=fb).status_code == 404
    client.cookies.clear()
    client.cookies.update(owner_cookie)
    assert client.get(f"/api/case/{run_id}").status_code == 200
    assert client.post("/api/feedback", json=fb).status_code == 200


def test_old_database_gets_owner_column(tmp_path, monkeypatch):
    # 升级前的库没有 owner 列：启动时补上，旧会话照常可用
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE run_meta (thread_id TEXT PRIMARY KEY, created_at REAL NOT NULL)"
    )
    con.execute("INSERT INTO run_meta VALUES ('" + "b" * 32 + "', 1e12)")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(path))

    async def scenario():
        await checkpoint.close()
        await checkpoint.get_saver()
        return await checkpoint.can_access("b" * 32, "lawyer01")

    assert asyncio.run(scenario())
    con = sqlite3.connect(path)
    assert "owner" in {row[1] for row in con.execute("PRAGMA table_info(run_meta)")}
    con.close()
