# 每日运行小结（#76）：按北京时间取前一天、阈值告警、Key 连通性、写文件与补写。日志数据均为虚构
import asyncio
import json
import os
import stat
import time
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from app import config
from app.services import daily_summary as ds
from app.services import run_log
from scripts import daily_summary as cli

DAY = date(2026, 10, 24)


def _line(event, ts="2026-10-24T02:00:00+00:00", run_id="r1", **kw):
    return json.dumps({"ts": ts, "event": event, "run_id": run_id, **kw}, ensure_ascii=False)


def _write_log(lines):
    with open(run_log.RUN_LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_day_boundary_is_beijing_time():
    lines = [
        _line("run_start", ts="2026-10-23T15:59:59+00:00"),  # 北京 10-23 23:59 → 前一天
        _line("run_start", ts="2026-10-23T16:00:00+00:00"),  # 北京 10-24 00:00
        _line("run_start", ts="2026-10-24T15:59:00+00:00"),  # 北京 10-24 23:59
        _line("run_start", ts="2026-10-24T16:00:00+00:00"),  # 北京 10-25
        "坏行",
    ]
    assert ds.collect(lines, DAY, {}).analyses == 2


def test_collect_counts_and_cost():
    lines = [
        _line("run_start"),
        _line("run_start", run_id="r2"),
        _line("resume"),
        _line("node", node="extract", outcome="error", error_category="llm_bad_json"),
        _line("ocr_batch", node="ocr_augment", requested=10, ok=7, failed=3, timeouts=1, rate_limited=0),
        _line("llm_fallback", reason="APITimeoutError", model="qwen-test"),
        _line("dashscope_key_switch", reason=403, path="ocr"),
        _line("llm_call", node="extract", model="m1", prompt_tokens=1_000_000, completion_tokens=0),
    ]
    s = ds.collect(lines, DAY, {"m1": {"input": 2.0, "output": 8.0}, "ocr_page": 0.1})
    assert (s.analyses, s.resumes, s.node_errors) == (2, 1, 1)
    assert (s.ocr_requested, s.ocr_failed, s.llm_fallbacks, s.key_switches) == (10, 3, 1, 1)
    assert s.cost == pytest.approx(2.0 + 0.7) and not s.unpriced
    assert s.errors == {"llm_bad_json": 1, "ocr_timeout": 1, "ocr_failed": 2}


def test_alerts_by_threshold(monkeypatch):
    monkeypatch.setattr(config, "ALERT_MAX_DAILY_COST", 1.0)
    s = ds.DayStats(day=DAY, node_errors=4, ocr_requested=20, ocr_failed=3, llm_fallbacks=4,
                    key_switches=1, cost=2.5)
    keys = [ds.KeyCheck("DEEPSEEK_API_KEY", "被拒（HTTP 401）", True), ds.KeyCheck("X", "正常", False)]
    found = ds.alerts(s, keys)
    assert len(found) == 6
    assert any("OCR 失败率 15%" in a for a in found)
    assert "DEEPSEEK_API_KEY：被拒（HTTP 401）" in found
    # 都在阈值内：不报
    quiet = ds.DayStats(day=DAY, node_errors=3, ocr_requested=19, ocr_failed=10, llm_fallbacks=3, cost=0.5)
    assert ds.alerts(quiet, []) == []  # OCR 页数不足 20 页不判失败率


def test_check_keys(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "虚构ds")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "虚构dash")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY_BACKUP", "虚构paid")
    seen = []

    def fake_get(url, headers, timeout):
        seen.append((url, headers["Authorization"]))
        key = headers["Authorization"].split()[1]
        if key == "虚构ds":
            raise requests.ConnectionError("x")
        resp = requests.Response()
        resp.status_code = {"虚构dash": 401, "虚构paid": 404}[key]
        return resp

    monkeypatch.setattr(requests, "get", fake_get)
    got = {k.name: (k.status, k.alert) for k in ds.check_keys()}
    assert got == {
        "DEEPSEEK_API_KEY": ("连不上（ConnectionError）", True),
        "DASHSCOPE_API_KEY": ("被拒（HTTP 401）", True),
        "DASHSCOPE_API_KEY_BACKUP": ("未能确认（HTTP 404）", False),
    }
    assert all(url.endswith("/models") for url, _ in seen)


def test_unconfigured_backup_key_is_skipped(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", None)
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY_BACKUP", None)
    got = [(k.name, k.status) for k in ds.check_keys()]
    assert got == [("DEEPSEEK_API_KEY", "未配置"), ("DASHSCOPE_API_KEY", "未配置")]


def test_run_for_writes_private_file_without_case_content(caplog):
    _write_log([
        _line("run_start", run_id="r1"),
        _line("node", node="extract", outcome="error", error_category="internal"),
        _line("node", node="extract", outcome="error", error_category="internal"),
        _line("node", node="extract", outcome="error", error_category="internal"),
        _line("node", node="extract", outcome="error", error_category="internal"),
        _line("lawyer_edits", fields=["defendant_name"], user="虚构律师"),
    ])
    path = ds.run_for(DAY, with_key_check=False)
    assert path.endswith("2026-10-24.md")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    text = open(path, encoding="utf-8").read()
    assert "## ⚠️ 需要关注" in text and "分析中断 4 次" in text
    assert "分析 1 次" in text
    assert "虚构律师" not in text and "defendant_name" not in text  # 不带账号、字段等信息
    assert "需要关注" in caplog.text


def test_purge_old_summaries(monkeypatch):
    monkeypatch.setattr(config, "RUN_LOG_RETENTION_DAYS", 30)
    os.makedirs(config.DAILY_SUMMARY_DIR)
    for name in ("2026-09-01.md", "2026-10-01.md", "说明.txt"):
        open(os.path.join(config.DAILY_SUMMARY_DIR, name), "w").close()
    assert ds.purge_old(today=DAY) == 1
    assert sorted(os.listdir(config.DAILY_SUMMARY_DIR)) == ["2026-10-01.md", "说明.txt"]


def _touch(day):
    os.makedirs(config.DAILY_SUMMARY_DIR, exist_ok=True)
    open(ds.summary_path(day), "w").close()


def test_due_days_waits_for_hour_and_skips_existing(monkeypatch):
    monkeypatch.setattr(config, "DAILY_SUMMARY_HOUR", 8)
    bj = timezone(timedelta(hours=8))
    # 刚上线、一份没写过：只写昨天，且要过 8 点
    assert ds.due_days(datetime(2026, 10, 25, 7, 59, tzinfo=bj)) == []
    assert ds.due_days(datetime(2026, 10, 25, 8, 0, tzinfo=bj)) == [DAY]
    assert ds.due_days(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)) == [DAY]  # UTC 00:30 = 北京 08:30
    _touch(DAY)
    assert ds.due_days(datetime(2026, 10, 25, 9, 0, tzinfo=bj)) == []  # 已写过不重复


def test_due_days_backfills_after_outage(monkeypatch):
    # 10-22 写过，服务停到 10-26 09:00 才恢复：10-23、10-24、10-25 逐日补
    monkeypatch.setattr(config, "DAILY_SUMMARY_HOUR", 8)
    _touch(date(2026, 10, 22))
    now = datetime(2026, 10, 26, 9, 0, tzinfo=ds.BEIJING)
    assert ds.due_days(now) == [date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25)]
    # 8 点前：昨天还没到期，只补到前天
    assert ds.due_days(now.replace(hour=7))[-1] == date(2026, 10, 24)
    # 最多回溯运行日志保留期
    monkeypatch.setattr(config, "RUN_LOG_RETENTION_DAYS", 2)
    assert ds.due_days(now) == [date(2026, 10, 24), date(2026, 10, 25)]


def test_partial_write_leaves_no_summary(monkeypatch):
    # 写到一半失败（如磁盘满）：不留正式文件、不留临时文件，下一轮仍判为该写
    def broken_fdopen(fd, *a, **k):
        os.close(fd)
        raise OSError("No space left on device")

    monkeypatch.setattr(ds.os, "fdopen", broken_fdopen)
    with pytest.raises(OSError):
        ds.deliver(DAY, "小结", [])
    assert os.listdir(config.DAILY_SUMMARY_DIR) == []
    assert ds.due_days(datetime(2026, 10, 25, 9, 0, tzinfo=ds.BEIJING)) == [DAY]


def test_log_rotated_while_reading_is_retried(monkeypatch):
    # 列出文件后当前日志被按天切分改名：重新列一次再读，不漏数据
    rotated = run_log.RUN_LOG_PATH.replace(".jsonl", "-2026-10-24.jsonl")
    with open(rotated, "w", encoding="utf-8") as f:
        f.write(_line("run_start") + "\n")
    calls = []

    def fake_log_files():
        calls.append(1)
        return [run_log.RUN_LOG_PATH] if len(calls) == 1 else [rotated]

    monkeypatch.setattr(run_log, "log_files", fake_log_files)
    assert ds.collect(ds._read_log_lines(), DAY, {}).analyses == 1


def test_unreadable_log_fails_run_without_writing(monkeypatch):
    monkeypatch.setattr(run_log, "log_files", lambda: [run_log.RUN_LOG_PATH + ".不存在"])
    with pytest.raises(OSError):
        ds.run_for(DAY, with_key_check=False)
    assert not os.path.exists(ds.summary_path(DAY))


def test_daily_loop_backfills_and_checks_keys_only_for_latest(monkeypatch):
    days = [date(2026, 10, 23), DAY]
    calls = []

    def fake_run_for(day, with_key_check):
        calls.append((day, with_key_check))

    monkeypatch.setattr(ds, "due_days", lambda _now: days if not calls else [])
    monkeypatch.setattr(ds, "run_for", fake_run_for)

    async def go():
        task = asyncio.create_task(ds.daily_loop(interval_s=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert calls == [(date(2026, 10, 23), False), (DAY, True)]


def test_daily_loop_survives_failure(monkeypatch, caplog):
    calls = []

    def boom(day, with_key_check):
        calls.append(day)
        raise OSError("磁盘已满")

    monkeypatch.setattr(ds, "due_days", lambda _now: [date(2026, 10, 23), DAY])
    monkeypatch.setattr(ds, "run_for", boom)

    async def go():
        task = asyncio.create_task(ds.daily_loop(interval_s=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert len(calls) >= 2 and DAY not in calls  # 某天失败就停在那天，下一轮从它重试
    assert "每日小结生成失败：OSError" in caplog.text


def test_cli_defaults_to_yesterday(monkeypatch, capsys):
    _write_log([_line("run_start")])
    got = {}

    def fake_run_for(day, with_key_check):
        got.update(day=day, check=with_key_check)
        path = ds.summary_path(day)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write("小结")
        return path

    monkeypatch.setattr(cli, "run_for", fake_run_for)
    assert cli.main(["--no-key-check"]) == 0
    assert got["day"] == datetime.now(ds.BEIJING).date() - timedelta(days=1) and got["check"] is False
    cli.main(["--date", "2026-10-24"])
    assert got["day"] == DAY and got["check"] is True
    assert "小结" in capsys.readouterr().out


def test_service_starts_daily_loop_when_enabled(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    seen = []
    monkeypatch.setattr(config, "DAILY_SUMMARY_ENABLED", True)
    monkeypatch.setattr(ds, "due_days", lambda now: seen.append(now) or [])
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        for _ in range(50):
            if seen:
                break
            time.sleep(0.01)
    assert seen  # 启动即检查一次
