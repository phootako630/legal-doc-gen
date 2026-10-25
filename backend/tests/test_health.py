# 健康检查分层（#76）：/healthz 只看进程，/readyz 看 checkpoint 库读写、磁盘余量与 Key 是否配置
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agent import checkpoint
from app.main import app
from app.services import readiness

_Usage = namedtuple("_Usage", "total used free")


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "虚构deepseek")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "虚构dashscope")


def test_healthz_is_liveness_only():
    c = TestClient(app)
    assert c.get("/healthz").json()["status"] == "ok"
    assert c.get("/").status_code == 200  # 旧地址保留


def test_readyz_ok(keys):
    with TestClient(app) as c:
        res = c.get("/readyz")
    assert res.status_code == 200, res.json()
    assert res.json()["ready"] is True
    assert all(item["ok"] for item in res.json()["checks"])


def test_readyz_missing_key_is_503(keys, monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", " ")
    res = TestClient(app).get("/readyz")
    assert res.status_code == 503
    failed = [c for c in res.json()["checks"] if not c["ok"]]
    assert failed == [{"name": "DASHSCOPE_API_KEY", "ok": False, "detail": "未配置"}]
    assert "虚构deepseek" not in res.text  # 不回显 key


def test_readyz_low_disk_is_503(keys, monkeypatch):
    monkeypatch.setattr(readiness.shutil, "disk_usage", lambda _p: _Usage(10**9, 10**9, 100 * 1024 * 1024))
    res = TestClient(app).get("/readyz")
    assert res.status_code == 503
    disk = [c for c in res.json()["checks"] if "磁盘" in c["name"]]
    assert len(disk) == 3 and not any(c["ok"] for c in disk)
    assert "剩余 100 MB，低于 500 MB" in disk[0]["detail"]


def test_readyz_db_failure_is_503(keys, monkeypatch):
    async def broken():
        raise OSError("磁盘已满")

    monkeypatch.setattr(checkpoint, "probe", broken)
    res = TestClient(app).get("/readyz")
    assert res.status_code == 503
    db = res.json()["checks"][0]
    assert db == {"name": "checkpoint 库读写", "ok": False, "detail": "失败（OSError）"}


def test_disk_check_walks_up_to_existing_dir(tmp_path):
    # 上传目录首次上传前还不存在，按已存在的上级目录量
    r = readiness.check_disk("x", str(tmp_path / "还没建" / "uploads"), 0)
    assert r.ok and r.detail.startswith("剩余")


def test_readyz_not_behind_login(keys, monkeypatch):
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    assert TestClient(app).get("/readyz").status_code == 200
