# 登录：账号 / 密码校验、会话 Cookie、未登录拦截、锁定、退出、修改密码、停用账号（账号均为虚构）
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import auth_store

PW = "correct-horse-1"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    auth_store.create_user("lawyer01", PW)


def _login(c: TestClient, username: str = "lawyer01", password: str = PW, ip: str = "1.2.3.4"):
    return c.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-Real-IP": ip},
    )


def test_disabled_auth_lets_everything_through():
    c = TestClient(app)
    assert c.get("/api/auth/me").json() == {"auth_enabled": False, "username": None}
    assert c.get("/api/branches").status_code == 200


def test_protected_api_requires_login(auth_on):
    c = TestClient(app)
    res = c.get("/api/branches")
    assert res.status_code == 401
    assert res.headers["X-Login-Required"] == "1"  # 前端据此跳登录页
    assert res.json()["detail"] == "请先登录"
    # 登录状态查询与健康检查不拦
    assert c.get("/api/auth/me").json() == {"auth_enabled": True, "username": None}
    assert c.get("/").status_code == 200


def test_login_sets_httponly_strict_cookie_and_unlocks_api(auth_on):
    c = TestClient(app)
    res = _login(c)
    assert res.status_code == 200 and res.json()["username"] == "lawyer01"
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # 测试走 http：不加 Secure，否则浏览器不收
    assert f"max-age={config.SESSION_DAYS * 86400}" in cookie
    assert c.get("/api/auth/me").json()["username"] == "lawyer01"
    assert c.get("/api/branches").status_code == 200


def test_cookie_is_secure_behind_https_proxy(auth_on):
    c = TestClient(app)
    res = c.post(
        "/api/auth/login",
        json={"username": "lawyer01", "password": PW},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "secure" in res.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    ("username", "password"), [("lawyer01", "wrong-password"), ("nobody", PW)]
)
def test_wrong_credentials_same_message(auth_on, username, password):
    # 账号不存在与密码错误给同样的提示，不暴露哪些账号存在
    res = _login(TestClient(app), username, password)
    assert res.status_code == 401 and res.json()["detail"] == "账号或密码不正确"
    assert "X-Login-Required" not in res.headers


def test_lockout_after_five_failures_per_ip_and_account(auth_on):
    c = TestClient(app)
    for _ in range(config.LOGIN_MAX_FAILURES):
        assert _login(c, password="nope-nope-1").status_code == 401
    locked = _login(c)  # 锁定期间密码对了也不行
    assert locked.status_code == 429 and "15 分钟" in locked.json()["detail"]
    # 别的 IP 不受影响：外网有人乱试，不会把律师本人锁在门外
    assert _login(c, ip="5.6.7.8").status_code == 200


def test_lockout_per_ip_across_accounts(auth_on):
    c = TestClient(app)
    for i in range(config.LOGIN_MAX_FAILURES_PER_IP):
        _login(c, username=f"guess{i}", password="nope-nope-1")
    assert _login(c).status_code == 429


def test_success_resets_account_failures(auth_on):
    c = TestClient(app)
    for _ in range(config.LOGIN_MAX_FAILURES - 1):
        _login(c, password="nope-nope-1")
    assert _login(c).status_code == 200
    for _ in range(config.LOGIN_MAX_FAILURES - 1):
        assert _login(c, password="nope-nope-1").status_code == 401


def test_logout_revokes_session(auth_on):
    c = TestClient(app)
    _login(c)
    token = c.cookies.get("session")
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/branches").status_code == 401
    # 即使有人留着旧令牌也已失效
    c2 = TestClient(app, cookies={"session": token})
    assert c2.get("/api/branches").status_code == 401


def test_expired_session_rejected(auth_on):
    c = TestClient(app)
    _login(c)
    with sqlite3.connect(config.AUTH_DB_PATH) as conn:
        conn.execute("UPDATE sessions SET expires_at = 0")
    assert c.get("/api/branches").status_code == 401


def test_change_password_keeps_current_session_and_kicks_others(auth_on):
    here, elsewhere = TestClient(app), TestClient(app)
    _login(here)
    _login(elsewhere)
    res = here.post(
        "/api/auth/change-password",
        json={"old_password": PW, "new_password": "brand-new-pass-2"},
    )
    assert res.status_code == 204
    assert here.get("/api/branches").status_code == 200
    assert elsewhere.get("/api/branches").status_code == 401
    assert _login(TestClient(app), password=PW).status_code == 401
    assert _login(TestClient(app), password="brand-new-pass-2").status_code == 200


@pytest.mark.parametrize(
    ("old", "new", "detail"),
    [
        ("wrong-old-pass", "brand-new-pass-2", "当前密码不正确"),
        (PW, "short", "密码至少 10 位"),
        (PW, PW, "新密码不能与当前密码相同"),
    ],
)
def test_change_password_rejections(auth_on, old, new, detail):
    c = TestClient(app)
    _login(c)
    res = c.post("/api/auth/change-password", json={"old_password": old, "new_password": new})
    assert res.status_code == 400 and res.json()["detail"] == detail


def test_change_password_requires_login(auth_on):
    res = TestClient(app).post(
        "/api/auth/change-password",
        json={"old_password": PW, "new_password": "brand-new-pass-2"},
    )
    assert res.status_code == 401


def test_disabled_user_is_logged_out_and_cannot_login(auth_on):
    c = TestClient(app)
    _login(c)
    auth_store.set_active("lawyer01", False)
    assert c.get("/api/branches").status_code == 401
    assert _login(TestClient(app)).status_code == 401
    auth_store.set_active("lawyer01", True)
    assert _login(TestClient(app)).status_code == 200


def test_store_never_keeps_plaintext(auth_on):
    c = TestClient(app)
    _login(c)
    token = c.cookies.get("session")
    raw = Path(config.AUTH_DB_PATH).read_bytes()
    assert PW.encode() not in raw and token.encode() not in raw
    assert oct(Path(config.AUTH_DB_PATH).stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("name", ["a", "has space", "x" * 33, "中文"])
def test_create_user_rejects_bad_names(name):
    with pytest.raises(auth_store.AuthError):
        auth_store.create_user(name, PW)


def test_create_duplicate_user():
    auth_store.create_user("dev01", PW)
    with pytest.raises(auth_store.AuthError, match="已存在"):
        auth_store.create_user("dev01", PW)


def test_manage_users_script(tmp_path):
    db = tmp_path / "script-auth.sqlite"
    script = Path(__file__).resolve().parents[1] / "scripts" / "manage_users.py"
    env = {"AUTH_DB_PATH": str(db), "PATH": "", "PYTHONIOENCODING": "utf-8"}

    def run(*args: str, stdin: str = ""):
        return subprocess.run(
            [sys.executable, str(script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            cwd=script.parents[1],
        )

    add = run("add", "lawyer02", stdin=f"{PW}\n{PW}\n")
    assert add.returncode == 0, add.stderr
    mismatch = run("passwd", "lawyer02", stdin=f"{PW}x\n{PW}y\n")
    assert mismatch.returncode != 0 and "不一致" in mismatch.stderr
    assert run("disable", "lawyer02").returncode == 0
    listing = run("list")
    assert "lawyer02" in listing.stdout and "已停用" in listing.stdout
    assert run("enable", "lawyer02").returncode == 0
    assert "已停用" not in run("list").stdout
