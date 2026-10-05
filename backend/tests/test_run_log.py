# run_log 测试：事件落盘、不泄露取值、节点/interrupt 记录、律师改动对比
import asyncio
import json
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.services import run_log


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _use_log(monkeypatch, tmp_path):
    path = tmp_path / "run.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    return path


def test_log_failure_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(tmp_path / "no_dir" / "x.jsonl"))
    run_log.log_event("x")  # 目录不存在：不抛异常


def test_agent_flow_logs_nodes_and_no_values(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    ok = {
        "has_approval": True,
        "has_contract": True,
        "has_acceptance": True,
        "can_proceed": True,
        "missing": [],
        "notes": "",
    }
    fields = {
        "total_amount": {"value": 900000, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "机密公司名", "src": "《审批表》"},
    }
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[ok, fields, "说明\n---HIGHLIGHT---\n- 1"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    asyncio.run(run_resume(state.run_id, {"total_amount": 894934}))

    ev = _events(path)
    assert {e["run_id"] for e in ev} == {state.run_id}
    interrupts = [e for e in ev if e["event"] == "node" and e["outcome"] == "interrupt"]
    assert interrupts and interrupts[0]["node"] == "validate"
    assert interrupts[0]["kind"] == "conflict"
    assert any(
        e["event"] == "resume" and e["decided_keys"] == ["total_amount"] for e in ev
    )
    raw = path.read_text(encoding="utf-8")
    assert "机密公司名" not in raw and "894934" not in raw


def test_lawyer_edits_only_keys(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    run_log.remember_fields(
        "r1", {"a": {"value": "x"}, "b": {"value": None}, "c": {"value": "k"}}
    )
    run_log.log_lawyer_edits(
        "r1", {"a": {"value": "y"}, "b": {"value": "z"}, "c": {"value": "k"}}
    )
    (e,) = _events(path)
    assert e["fields"] == {"a": "modified", "b": "filled"}
    assert "y" not in path.read_text(encoding="utf-8").replace("lawyer_edits", "")
