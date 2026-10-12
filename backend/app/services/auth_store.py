# 账号与登录会话存储（SQLite）：密码只存加盐 scrypt 哈希，会话令牌只存 SHA-256 摘要。
#
# 框架无关的纯同步函数，供 routers/auth.py 与 scripts/manage_users.py 共用。
# 库文件与案件库分开（config.AUTH_DB_PATH），不随案件过期清除；目录 0700、文件 0600。
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

from app import config

USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{2,32}")

# scrypt 参数：单次校验约几十毫秒，足以拖慢离线暴力破解，又不影响登录体验
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 2**14, 8, 1, 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    password_changed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
"""


class AuthError(ValueError):
    """面向使用者的中文错误（账号格式、密码长度、账号不存在等）。"""


@dataclass
class UserInfo:
    username: str
    active: bool
    created_at: float
    password_changed_at: float


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# 账号不存在时也做一次同等耗时的校验，避免靠响应时间判断账号是否存在
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def validate_new_password(password: str) -> None:
    if len(password) < config.PASSWORD_MIN_LENGTH:
        raise AuthError(f"密码至少 {config.PASSWORD_MIN_LENGTH} 位")
    if len(password) > 128:
        raise AuthError("密码不能超过 128 位")


def _connect() -> sqlite3.Connection:
    path = config.AUTH_DB_PATH
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── 账号管理（服务器上用 scripts/manage_users.py 调用）──────────────────────────


def create_user(username: str, password: str) -> None:
    if not USERNAME_RE.fullmatch(username):
        raise AuthError("账号只能用 2–32 位字母、数字、_ . -")
    validate_new_password(password)
    now = time.time()
    with closing(_connect()) as conn, conn:
        try:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, 1, ?, ?)",
                (username, hash_password(password), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise AuthError(f"账号「{username}」已存在") from exc


def set_password(username: str, password: str, keep_token: str | None = None) -> None:
    """改密码并让该账号其他已登录的会话失效（keep_token 对应的当前会话保留）。"""
    validate_new_password(password)
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE username = ?",
            (hash_password(password), time.time(), username),
        )
        if cur.rowcount == 0:
            raise AuthError(f"账号「{username}」不存在")
        _delete_sessions(conn, username, keep_token)


def set_active(username: str, active: bool) -> None:
    """停用 / 启用账号；停用时立即踢掉该账号所有会话。"""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE users SET active = ? WHERE username = ?", (int(active), username)
        )
        if cur.rowcount == 0:
            raise AuthError(f"账号「{username}」不存在")
        if not active:
            _delete_sessions(conn, username, None)


def list_users() -> list[UserInfo]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT username, active, created_at, password_changed_at FROM users ORDER BY username"
        ).fetchall()
    return [UserInfo(r[0], bool(r[1]), r[2], r[3]) for r in rows]


# ── 登录与会话 ──────────────────────────────────────────────────────────────────


def check_password(username: str, password: str) -> bool:
    """账号存在、已启用且密码正确才返回 True；任何失败的耗时都相同。"""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT password_hash, active FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return False
    ok = verify_password(password, row[0])
    return ok and bool(row[1])


def create_session(username: str) -> str:
    """新建会话，返回给浏览器的明文令牌（库里只存摘要）；顺手清掉已过期的会话。"""
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + config.SESSION_DAYS * 86400
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (_token_hash(token), username, now, expires),
        )
    return token


def session_user(token: str | None) -> str | None:
    """令牌对应的账号；令牌无效、过期或账号已停用都返回 None。"""
    if not token:
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT s.username FROM sessions s JOIN users u ON u.username = s.username "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (_token_hash(token), time.time()),
        ).fetchone()
    return row[0] if row else None


def delete_session(token: str | None) -> None:
    if not token:
        return
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def _delete_sessions(conn: sqlite3.Connection, username: str, keep_token: str | None) -> None:
    if keep_token:
        conn.execute(
            "DELETE FROM sessions WHERE username = ? AND token_hash != ?",
            (username, _token_hash(keep_token)),
        )
    else:
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
