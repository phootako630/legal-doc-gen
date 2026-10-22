# run_log 测试：事件落盘、不泄露取值、节点/interrupt 记录、律师改动对比
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from app.agent.runner import run_analyze, run_resume
from app.routers.generate import GenerateRequest, generate
from app.services import run_log
from scripts import usage_report


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
    run_log.log_lawyer_edits(
        "r1",
        {"a": {"value": "x"}, "b": {"value": None}, "c": {"value": "k"}},
        {"a": {"value": "y"}, "b": {"value": "z"}, "c": {"value": "k"}},
    )
    (e,) = _events(path)
    assert e["fields"] == {"a": "modified", "b": "filled"}
    assert "y" not in path.read_text(encoding="utf-8").replace("lawyer_edits", "")


def test_lawyer_edits_without_snapshot_is_recorded(monkeypatch, tmp_path):
    # 会话已过期 / 不存在：不静默丢，记一条 unavailable
    path = _use_log(monkeypatch, tmp_path)
    run_log.log_lawyer_edits("r1", None, {"a": {"value": "y"}})
    (e,) = _events(path)
    assert e["event"] == "lawyer_edits_unavailable" and e["run_id"] == "r1"


def test_lawyer_edits_survive_restart(monkeypatch, tmp_path):
    # 交给律师时的字段存在 checkpoint 里：服务重启（图与连接全部重建）后生成起诉状仍能算出改动
    path = _use_log(monkeypatch, tmp_path)
    fields = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
        "defendant_name": {"value": "某被告", "src": "《审批表》"},
    }
    ok = {"can_proceed": True, "missing": [], "notes": ""}
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[ok, fields, "说明\n---HIGHLIGHT---\n- 1"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is None
    asyncio.run(reset_graph())  # 模拟重启：进程内缓存全部丢弃

    final = {k: dict(v) for k, v in state.extracted_fields.items()}
    final["defendant_name"]["value"] = "律师改过的被告"
    asyncio.run(generate(GenerateRequest(validated_json=final, run_id=state.run_id)))

    (e,) = [e for e in _events(path) if e["event"].startswith("lawyer_edits")]
    assert e["event"] == "lawyer_edits"
    assert e["fields"] == {"defendant_name": "modified"}


def _age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_log_rotates_on_new_day(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    run_log.log_event("old")
    _age(path, 1)
    yesterday = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date()
    run_log.log_event("new")

    rotated = tmp_path / f"run-{yesterday.isoformat()}.jsonl"
    assert [e["event"] for e in _events(rotated)] == ["old"]
    assert [e["event"] for e in _events(path)] == ["new"]
    assert run_log.log_files() == [str(rotated), str(path)]


def test_purge_old_removes_expired_rotations(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    today = datetime.now(timezone.utc).date()
    old = tmp_path / f"run-{(today - timedelta(days=100)).isoformat()}.jsonl"
    recent = tmp_path / f"run-{(today - timedelta(days=3)).isoformat()}.jsonl"
    for f in (old, recent):
        f.write_text("{}\n", encoding="utf-8")
    run_log.log_event("today")

    assert run_log.purge_old(retention_days=90) == 1
    assert not old.exists() and recent.exists() and path.exists()


def test_usage_report_reads_rotated_files(tmp_path):
    path = tmp_path / "run_log.jsonl"
    (tmp_path / "run_log-2026-01-02.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "run_log-2026-01-01.jsonl").write_text("", encoding="utf-8")
    path.write_text("", encoding="utf-8")
    assert usage_report.log_files(str(path)) == [
        str(tmp_path / "run_log-2026-01-01.jsonl"),
        str(tmp_path / "run_log-2026-01-02.jsonl"),
        str(path),
    ]
