# Agent checkpoint 的 SQLite 持久化：连接生命周期、会话登记、过期清理。
#
# 为什么落盘：in-memory checkpointer 在服务重启后丢失所有进行中的案件（resume 404）。
# 单进程、少量律师使用，SQLite 足够；将来多实例时只需替换这里的 saver 实现，
# 图与节点层零改动（见 CLAUDE.md「未来接 DB 时零重构」）。
#
# 保密：checkpoint 里含案件字段与全文，属客户机密。库文件 0600（SQLite 的 -wal/-shm
# 会沿用主库权限），且不依赖 umask；run_meta 记录每个会话的创建时间，供 purge_expired 按
# CASE_RETENTION_DAYS 连同 checkpoint 一并清除，is_expired 供 resume 时拒绝过期会话。
#
# 幂等：analyze_keys 记录「请求编号（Idempotency-Key）→ 会话」，同一编号的重复分析请求
# 直接返回已有会话，不再重跑 OCR / LLM（见 runner.run_analyze_once）。随会话一起过期清除。
from __future__ import annotations

import asyncio
import os
import time

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import config

_conn: aiosqlite.Connection | None = None
_saver: AsyncSqliteSaver | None = None
# saver 内部的 asyncio.Lock 绑定创建它时的事件循环；服务里只有一个循环，不受影响。
# 记下创建时的循环，若换了循环（如测试里多次 asyncio.run）就重连——状态都在 SQLite 文件里，不丢。
_loop: asyncio.AbstractEventLoop | None = None
# 初始化锁同样按事件循环各建一把，防止并发的首批请求各开一个连接
_init_lock: asyncio.Lock | None = None
_init_lock_loop: asyncio.AbstractEventLoop | None = None


def _lock_for(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    global _init_lock, _init_lock_loop
    if _init_lock is None or _init_lock_loop is not loop:
        _init_lock, _init_lock_loop = asyncio.Lock(), loop
    return _init_lock


async def get_saver() -> AsyncSqliteSaver:
    """懒初始化并缓存 saver（首次调用时建库建表）。路径取自 config，测试可改写。"""
    global _conn, _saver, _loop
    running = asyncio.get_running_loop()
    async with _lock_for(running):
        if _saver is not None and _loop is running:
            return _saver
        if _saver is not None:
            await close()  # 换了事件循环：关掉旧连接，下面重新打开同一个文件
        path = config.CHECKPOINT_DB_PATH
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
            # 先以 0600 建好空库文件，SQLite 创建 -wal/-shm 时会沿用它的权限；
            # 库文件已存在（含旧版本留下的）也一并收紧
            os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
            os.chmod(path, 0o600)
        conn = await aiosqlite.connect(path)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")  # 读写互不阻塞，崩溃后更安全
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS run_meta "
                "(thread_id TEXT PRIMARY KEY, created_at REAL NOT NULL)"
            )
            # fingerprint：请求内容的摘要，防止同一编号被误用于另一组材料
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS analyze_keys "
                "(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "run_id TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            await conn.commit()
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
        except BaseException:
            # 初始化失败必须关连接：aiosqlite 的工作线程不是 daemon，漏关会让进程无法退出
            await conn.close()
            raise
        _conn, _saver, _loop = conn, saver, running
        return saver


async def close() -> None:
    """关闭连接并重置缓存（服务关闭 / 测试隔离用）。"""
    global _conn, _saver, _loop
    conn, _conn, _saver, _loop = _conn, None, None, None
    if conn is not None:
        await conn.close()


async def record_run(run_id: str) -> None:
    """登记会话创建时间，作为过期清理的依据。"""
    await get_saver()
    assert _conn is not None
    await _conn.execute(
        "INSERT OR IGNORE INTO run_meta (thread_id, created_at) VALUES (?, ?)",
        (run_id, time.time()),
    )
    await _conn.commit()


async def is_expired(run_id: str, retention_days: float | None = None) -> bool:
    """会话是否已超过保留期。没有登记记录的视为未过期（由 checkpoint 是否存在另行判断）。"""
    await get_saver()
    assert _conn is not None
    days = config.CASE_RETENTION_DAYS if retention_days is None else retention_days
    async with _conn.execute(
        "SELECT created_at FROM run_meta WHERE thread_id = ?", (run_id,)
    ) as cur:
        row = await cur.fetchone()
    return row is not None and row[0] < time.time() - days * 86400


async def purge_expired(retention_days: float | None = None) -> int:
    """删除超过保留期的会话（checkpoint + 登记），返回删除数量。"""
    saver = await get_saver()
    assert _conn is not None
    days = config.CASE_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = time.time() - days * 86400
    async with _conn.execute(
        "SELECT thread_id FROM run_meta WHERE created_at < ?", (cutoff,)
    ) as cur:
        expired = [row[0] async for row in cur]
    for thread_id in expired:
        await saver.adelete_thread(thread_id)
        await _conn.execute("DELETE FROM run_meta WHERE thread_id = ?", (thread_id,))
        await _conn.execute("DELETE FROM analyze_keys WHERE run_id = ?", (thread_id,))
    # 兜底：会话登记已不在的过期编号也一并清掉
    await _conn.execute("DELETE FROM analyze_keys WHERE created_at < ?", (cutoff,))
    await _conn.commit()
    return len(expired)


async def find_analyze_key(key: str) -> tuple[str, str] | None:
    """查请求编号对应的 (fingerprint, run_id)；没有则 None。"""
    await get_saver()
    assert _conn is not None
    async with _conn.execute(
        "SELECT fingerprint, run_id FROM analyze_keys WHERE key = ?", (key,)
    ) as cur:
        row = await cur.fetchone()
    return (row[0], row[1]) if row else None


async def save_analyze_key(key: str, fingerprint: str, run_id: str) -> None:
    """分析成功后记下「请求编号 → 会话」。"""
    await get_saver()
    assert _conn is not None
    await _conn.execute(
        "INSERT OR REPLACE INTO analyze_keys (key, fingerprint, run_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (key, fingerprint, run_id, time.time()),
    )
    await _conn.commit()


async def forget_analyze_key(key: str) -> None:
    """会话已不存在（过期 / 被清除）时删掉编号，让下一次请求重新分析。"""
    await get_saver()
    assert _conn is not None
    await _conn.execute("DELETE FROM analyze_keys WHERE key = ?", (key,))
    await _conn.commit()
