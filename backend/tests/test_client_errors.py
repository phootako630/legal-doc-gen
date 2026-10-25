# 前端错误上报（#76）：只收枚举与状态码、不收消息原文，写运行日志，按来源限流
import json
import os
from datetime import date

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import daily_summary as ds
from app.services import run_log


def _events(name):
    with open(run_log.RUN_LOG_PATH, encoding="utf-8") as f:
        return [e for e in map(json.loads, f) if e["event"] == name]


def test_report_is_logged():
    c = TestClient(app)
    res = c.post("/api/client-error", json={"kind": "api_5xx", "step": "review", "run_id": "a" * 32,
                                            "api": "resume", "status": 502})
    assert res.status_code == 204
    (ev,) = _events("client_error")
    assert ev["run_id"] == "a" * 32
    assert (ev["kind"], ev["step"], ev["api"], ev["status"]) == ("api_5xx", "review", "resume", 502)


def test_message_text_is_rejected():
    # 多余字段（如错误消息原文）一律 422，日志里什么都不记
    c = TestClient(app)
    res = c.post("/api/client-error", json={"kind": "uncaught", "step": "review",
                                            "message": "Cannot read 虚构置业有限公司"})
    assert res.status_code == 422
    assert not os.path.exists(run_log.RUN_LOG_PATH) or not _events("client_error")


def test_invalid_enum_and_status_rejected():
    c = TestClient(app)
    assert c.post("/api/client-error", json={"kind": "虚构", "step": "review"}).status_code == 422
    assert c.post("/api/client-error", json={"kind": "api_5xx", "step": "review", "status": 404}).status_code == 422
    assert c.post("/api/client-error", json={"kind": "uncaught", "step": "review", "run_id": "带 空格"}).status_code == 422
    # run_id 只认真实会话编号格式（32 位小写十六进制），夹带文字的一律拒绝
    for bad in ("defendant-name_XG-0815", "A" * 32, "a" * 31):
        assert c.post("/api/client-error", json={"kind": "uncaught", "step": "review", "run_id": bad}).status_code == 422


def test_rate_limited_per_source():
    c = TestClient(app)
    body = {"kind": "uncaught", "step": "upload"}
    codes = [c.post("/api/client-error", json=body).status_code for _ in range(22)]
    assert codes[:20] == [204] * 20 and codes[20:] == [429, 429]
    assert len(_events("client_error")) == 20
    # 另一个来源（不同 IP）不受影响
    assert c.post("/api/client-error", json=body, headers={"X-Real-IP": "10.0.0.9"}).status_code == 204


def test_requires_login_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    assert TestClient(app).post("/api/client-error", json={"kind": "uncaught", "step": "upload"}).status_code == 401


def test_daily_summary_counts_client_errors():
    lines = [json.dumps({"ts": "2026-10-24T02:00:00+00:00", "event": "client_error", "kind": "uncaught"})]
    s = ds.collect(lines, date(2026, 10, 24), {})
    assert s.client_errors == 1
    assert "前端报错（未捕获异常、接口 5xx）1 次" in ds.render(s, [], [], "")
