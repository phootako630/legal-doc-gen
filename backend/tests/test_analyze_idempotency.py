# 分析接口幂等：同一请求编号（Idempotency-Key）只真正分析一次。
#
# 用 AsyncMock 代替 LLM：side_effect 只准备「一次分析」所需的回复，
# 若重复请求又跑了一遍分析，mock 会因回复用尽而报错，测试即失败。
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.agent import checkpoint
from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from app.agent import runner
from app.agent.runner import IdempotencyConflict, run_analyze_once

_FILES = [{"filename": "审批表.pdf", "text": "虚构审批表"}]
_CHECKLIST = {"can_proceed": True, "missing": [], "notes": ""}
_FIELDS = {
    "total_amount": {"value": 894934, "src": "《审批表》"},
    "paid_amount": {"value": 638667.2, "src": "《审批表》"},
    "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
    "defendant_name": {"value": "某被告", "src": "《审批表》"},
}
_PROSE = "无冲突\n---HIGHLIGHT---\n"


def _one_run():
    """恰好够一次完整分析的 LLM 回复：清点、抽取、校验说明。"""
    return [_CHECKLIST, _FIELDS, _PROSE]


def _patch_chat(monkeypatch, side_effect) -> AsyncMock:
    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(nodes_mod, "chat", mock)
    return mock


def test_same_key_twice_runs_once(monkeypatch):
    chat = _patch_chat(monkeypatch, _one_run())

    async def scenario():
        first = await run_analyze_once("key-sequential-01", _FILES, True)
        second = await run_analyze_once("key-sequential-01", _FILES, True)
        return first, second

    first, second = asyncio.run(scenario())
    assert second.run_id == first.run_id
    assert chat.await_count == 3  # 只有第一次调用了 LLM


def test_concurrent_same_key_shares_one_run(monkeypatch):
    chat = _patch_chat(monkeypatch, _one_run())

    async def scenario():
        # 两个请求同时到达（如双击、网络重发）：应合并成一次分析
        return await asyncio.gather(
            run_analyze_once("key-concurrent-01", _FILES, True),
            run_analyze_once("key-concurrent-01", _FILES, True),
        )

    a, b = asyncio.run(scenario())
    assert a.run_id == b.run_id
    assert chat.await_count == 3


def test_different_keys_run_separately(monkeypatch):
    chat = _patch_chat(monkeypatch, _one_run() + _one_run())

    async def scenario():
        a = await run_analyze_once("key-separate-aa", _FILES, True)
        b = await run_analyze_once("key-separate-bb", _FILES, True)
        return a, b

    a, b = asyncio.run(scenario())
    assert a.run_id != b.run_id
    assert chat.await_count == 6


def test_same_key_with_other_materials_is_rejected(monkeypatch):
    _patch_chat(monkeypatch, _one_run())
    other = [{"filename": "审批表.pdf", "text": "另一案的审批表"}]

    async def scenario():
        await run_analyze_once("key-conflict-01", _FILES, True)
        await run_analyze_once("key-conflict-01", other, True)

    with pytest.raises(IdempotencyConflict):
        asyncio.run(scenario())


def test_failed_run_is_not_remembered(monkeypatch):
    # 第一次抽取阶段 LLM 报错 → 分析失败；同编号重试应重新分析并成功
    chat = _patch_chat(
        monkeypatch, [_CHECKLIST, RuntimeError("模拟 LLM 故障")] + _one_run()
    )

    async def scenario():
        with pytest.raises(RuntimeError):
            await run_analyze_once("key-failure-01", _FILES, True)
        return await run_analyze_once("key-failure-01", _FILES, True)

    case = asyncio.run(scenario())
    assert case.run_id
    assert chat.await_count == 5  # 失败的 2 次 + 重试的完整 3 次


def test_replay_survives_restart(monkeypatch):
    chat = _patch_chat(monkeypatch, _one_run())
    first = asyncio.run(run_analyze_once("key-restart-01", _FILES, True))

    # 模拟服务重启：关闭 checkpoint 连接、丢弃图缓存（进程内的运行记录也随之失效）
    asyncio.run(reset_graph())
    second = asyncio.run(run_analyze_once("key-restart-01", _FILES, True))
    assert second.run_id == first.run_id
    assert chat.await_count == 3


def test_expired_key_is_purged_and_reanalyzed(monkeypatch):
    chat = _patch_chat(monkeypatch, _one_run() + _one_run())

    async def scenario():
        first = await run_analyze_once("key-expired-01", _FILES, True)
        await checkpoint.purge_expired(retention_days=-1)  # 让所有会话都算过期
        assert await checkpoint.find_analyze_key("key-expired-01") is None
        second = await run_analyze_once("key-expired-01", _FILES, True)
        return first, second

    first, second = asyncio.run(scenario())
    assert second.run_id != first.run_id  # 过期后按新请求重新分析
    assert chat.await_count == 6


def test_http_header_validation_and_conflict(monkeypatch):
    from app.main import app

    _patch_chat(monkeypatch, _one_run())
    client = TestClient(app)
    body = {"files": _FILES, "internet_allowed": True}

    for bad_key in ("short", "has space in key", "x" * 129):
        bad = client.post(
            "/api/analyze", json=body, headers={"Idempotency-Key": bad_key}
        )
        assert bad.status_code == 400

    ok = client.post(
        "/api/analyze", json=body, headers={"Idempotency-Key": "key-http-0001"}
    )
    assert ok.status_code == 200
    again = client.post(
        "/api/analyze", json=body, headers={"Idempotency-Key": "key-http-0001"}
    )
    assert again.json()["run_id"] == ok.json()["run_id"]

    other = {
        "files": [{"filename": "x.pdf", "text": "另一案"}],
        "internet_allowed": True,
    }
    clash = client.post(
        "/api/analyze", json=other, headers={"Idempotency-Key": "key-http-0001"}
    )
    assert clash.status_code == 409


def test_without_header_each_request_is_new(monkeypatch):
    # 不带请求编号的旧客户端：行为不变，每次都是新分析
    from app.main import app

    chat = _patch_chat(monkeypatch, _one_run() + _one_run())
    client = TestClient(app)
    body = {"files": _FILES, "internet_allowed": True}
    a = client.post("/api/analyze", json=body).json()
    b = client.post("/api/analyze", json=body).json()
    assert a["run_id"] != b["run_id"]
    assert chat.await_count == 6


def test_dropped_request_does_not_cancel_analysis(monkeypatch):
    # 律师那边断网 / 代理超时：第一次请求被取消，但分析应继续；同编号重试接上它，不重跑
    replies = _one_run()
    gate = asyncio.Event()
    calls = 0

    async def slow_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await gate.wait()  # 模拟耗时的 LLM 调用
        return replies.pop(0)

    monkeypatch.setattr(nodes_mod, "chat", slow_chat)

    async def scenario():
        first = asyncio.create_task(run_analyze_once("key-dropped-01", _FILES, True))
        await asyncio.sleep(0.05)  # 分析已开始，卡在第一次 LLM 调用
        first.cancel()  # 第一次请求断开
        with pytest.raises(asyncio.CancelledError):
            await first
        gate.set()  # LLM 返回
        return await run_analyze_once("key-dropped-01", _FILES, True)  # 重试

    case = asyncio.run(scenario())
    assert case.run_id
    assert calls == 3  # 只跑了一次完整分析


def test_get_case_returns_current_state_and_404s(monkeypatch):
    """页面刷新后按 run_id 取回会话；未知 / 格式不对 / 已过期一律 404。"""
    from app.main import app

    _patch_chat(monkeypatch, _one_run())
    client = TestClient(app)
    body = {"files": _FILES, "internet_allowed": True}
    run_id = client.post(
        "/api/analyze", json=body, headers={"Idempotency-Key": "key-getcase-01"}
    ).json()["run_id"]

    got = client.get(f"/api/case/{run_id}")
    assert got.status_code == 200
    assert got.json()["run_id"] == run_id
    assert got.json()["extracted_fields"]["defendant_name"]["value"] == "某被告"

    assert client.get(f"/api/case/{'0' * 32}").status_code == 404
    assert client.get("/api/case/not-a-run-id").status_code == 404

    asyncio.run(checkpoint.purge_expired(retention_days=-1))
    assert client.get(f"/api/case/{run_id}").status_code == 404


def test_result_by_key_joins_running_analysis(monkeypatch):
    # 处理页刷新后按编号接回：还在跑就等同一个任务，不另起分析；/busy 期间计数为 1
    gate = asyncio.Event()
    replies = iter(_one_run())

    async def slow_chat(*_a, **_k):
        await gate.wait()
        return next(replies)

    monkeypatch.setattr(nodes_mod, "chat", slow_chat)

    async def scenario():
        first = asyncio.create_task(run_analyze_once("key-rejoin-0001", _FILES, True))
        await asyncio.sleep(0.05)
        assert runner.active_runs() == 1
        joined = asyncio.create_task(runner.analyze_result("key-rejoin-0001"))
        await asyncio.sleep(0.05)
        gate.set()
        return await first, await joined

    first, joined = asyncio.run(scenario())
    assert joined.run_id == first.run_id
    assert runner.active_runs() == 0


def test_result_by_key_after_done_and_unknown(monkeypatch):
    _patch_chat(monkeypatch, _one_run())
    first = asyncio.run(run_analyze_once("key-rejoin-0002", _FILES, True))
    again = asyncio.run(runner.analyze_result("key-rejoin-0002"))
    assert again.run_id == first.run_id  # 已跑完：返回会话状态，不重跑
    assert asyncio.run(runner.analyze_result("key-never-started")) is None

    from app.main import app

    client = TestClient(app)
    res = client.get("/api/analyze/result", params={"key": "key-never-started"})
    assert res.status_code == 404 and "重新上传" in res.json()["detail"]
    assert client.get("/api/analyze/result", params={"key": "bad key!"}).status_code == 400
    assert client.get("/busy").json() == {"running": 0}
