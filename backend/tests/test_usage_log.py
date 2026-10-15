# 运行日志带账号名：登录中间件设置、后台分析任务继承、未登录不写；统计脚本按账号 / 日期汇总（账号均为虚构）
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from app import config
from app.routers import auth as auth_router
from app.services import auth_store, run_log

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "usage_report.py"


def _use_log(monkeypatch, tmp_path):
    path = tmp_path / "run.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    return path


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _request(token: str | None):
    return SimpleNamespace(
        state=SimpleNamespace(),
        cookies={auth_router.COOKIE_NAME: token} if token else {},
        url=SimpleNamespace(path="/api/analyze"),
    )


def test_middleware_user_reaches_request_and_background_task(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    auth_store.create_user("lawyer01", "correct-horse-1")
    token = auth_store.create_session("lawyer01")

    async def call_next(_request):
        run_log.log_event("in_request")
        # 分析在 create_task 里跑，复制当时的上下文
        await asyncio.create_task(asyncio.to_thread(run_log.log_event, "in_task"))
        return "ok"

    async def go():
        assert await auth_router.auth_middleware(_request(token), call_next) == "ok"

    asyncio.run(go())
    assert [(e["event"], e["user"]) for e in _events(path)] == [
        ("in_request", "lawyer01"),
        ("in_task", "lawyer01"),
    ]


def test_no_user_field_without_login(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)

    async def go():
        run_log.log_event("bare")

    asyncio.run(go())
    assert "user" not in _events(path)[0]


def _line(ts: str, event: str, user: str | None = None, **data) -> str:
    rec = {"ts": ts, "run_id": "r", "event": event, **data}
    if user:
        rec["user"] = user
    return json.dumps(rec, ensure_ascii=False)


def test_usage_report_groups_by_user_and_cst_date(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_text(
        "\n".join(
            [
                # 16:30 UTC = 北京时间次日 00:30
                _line("2026-10-01T16:30:00+00:00", "run_start", "lawyer01"),
                _line(
                    "2026-10-02T01:00:00+00:00",
                    "llm_call",
                    "lawyer01",
                    prompt_tokens=100,
                    completion_tokens=20,
                ),
                _line(
                    "2026-10-02T01:00:01+00:00",
                    "ocr_batch",
                    "lawyer01",
                    requested=4,
                    ok=3,
                ),
                _line(
                    "2026-10-02T01:05:00+00:00",
                    "resume",
                    "lawyer01",
                    decided_keys=["a"],
                ),
                _line("2026-10-02T01:06:00+00:00", "lawyer_edits", "lawyer01", edited=0),
                _line("2026-10-02T02:00:00+00:00", "run_start", "lawyer02"),
                _line("2026-10-02T02:00:00+00:00", "feedback", "lawyer02"),
                _line("2026-10-02T03:00:00+00:00", "run_start"),
                "不是 JSON",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [sys.executable, "-I", str(_SCRIPT), str(log), "--days", "0"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "lawyer01\t2026-10-02\t1\t1\t1\t0\t120\t3" in out
    assert "lawyer02\t2026-10-02\t1\t0\t0\t1\t0\t0" in out
    assert "（未登录 / 旧记录）\t2026-10-02\t1" in out
    assert "合计" in out


def test_usage_report_missing_log(tmp_path):
    res = subprocess.run(
        [sys.executable, "-I", str(_SCRIPT), str(tmp_path / "none.jsonl")],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1 and "找不到运行日志" in res.stdout
