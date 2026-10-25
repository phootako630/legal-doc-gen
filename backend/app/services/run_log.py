# 运行日志：agent 每次运行的结构化事件，追加写入本地 JSONL，供复盘耗时/成本/律师改动率
#
# 保密约束：案件材料是客户机密，日志只记字段 key、数量、耗时、token，绝不记字段取值或原文
# （tests/test_log_secrecy.py 跑完整流程守着这条）。日志写入失败一律吞掉——可观测性不能影响主流程。
#
# 切分与保留：按天切分，跨天后的第一次写入把旧文件改名为 run_log-YYYY-MM-DD.jsonl（UTC 日期），
# 超过 RUN_LOG_RETENTION_DAYS 的切分文件由 purge_old 删除（服务的定时清理循环调用）。
from __future__ import annotations

import functools
import glob
import json
import os
import threading
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.config import RUN_LOG_ENABLED, RUN_LOG_PATH, RUN_LOG_RETENTION_DAYS
from app.services.error_category import categorize

# 当前协程所属的 run_id；runner 入口设置，节点/LLM 调用无需逐层传参
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
# 当前请求的登录账号；登录中间件设置，请求内启动的后台分析任务（create_task 复制上下文）一并继承。
# 只记账号名，用于统计谁在什么时候用了几次，不涉及案件内容
_user: ContextVar[str | None] = ContextVar("user", default=None)
# 当前图节点名（traced_node 设置）：节点里的 LLM / OCR 事件自动带上 node，按节点归集成本与耗时
_node: ContextVar[str | None] = ContextVar("node", default=None)
# 最近一次加载的提示词「文件名@模板哈希」（prompt_loader 设置），llm_call 据此记录提示词版本
_prompt: ContextVar[str | None] = ContextVar("prompt", default=None)
_lock = threading.Lock()


def set_run_id(run_id: str | None) -> None:
    _run_id.set(run_id)


def set_user(user: str | None) -> None:
    _user.set(user)


def set_prompt(tag: str | None) -> None:
    _prompt.set(tag)


def current_prompt() -> str | None:
    return _prompt.get()


def current_user() -> str | None:
    """当前请求的登录账号（登录中间件设置；未开启登录时为 None）。路由据此按账号隔离案件。"""
    return _user.get()


def log_event(event: str, run_id: str | None = None, **data: Any) -> None:
    """追加一条事件；未启用或写失败时静默返回。"""
    if not RUN_LOG_ENABLED:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": run_id or _run_id.get(),
        "event": event,
        **data,
    }
    user = _user.get()
    if user is not None:
        record["user"] = user
    node = _node.get()
    if node is not None and "node" not in record:
        record["node"] = node
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            _rotate_if_new_day(RUN_LOG_PATH)
            with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except (OSError, TypeError, ValueError):
        pass


def _rotated_path(path: str, day: date) -> str:
    stem, ext = os.path.splitext(path)
    return f"{stem}-{day.isoformat()}{ext}"


def _utc_day(ts: float) -> date:
    return datetime.fromtimestamp(ts, timezone.utc).date()


def _rotate_if_new_day(path: str) -> None:
    """当前文件最后写入是在前一天（UTC）→ 改名为带日期的切分文件，今天另起新文件。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return  # 还没有日志文件
    day = _utc_day(mtime)
    if day == _utc_day(time.time()):
        return
    target = _rotated_path(path, day)
    if os.path.exists(target):  # 同一天已切过（如时钟回拨）：追加，不覆盖
        with (
            open(path, encoding="utf-8") as src,
            open(target, "a", encoding="utf-8") as dst,
        ):
            dst.write(src.read())
        os.remove(path)
    else:
        os.replace(path, target)


def log_files(path: str | None = None) -> list[str]:
    """当前日志及全部切分文件，按时间从旧到新（统计脚本读全部）。"""
    path = path or RUN_LOG_PATH
    stem, ext = os.path.splitext(path)
    rotated = sorted(glob.glob(f"{glob.escape(stem)}-????-??-??{glob.escape(ext)}"))
    return rotated + ([path] if os.path.exists(path) else [])


def purge_old(retention_days: float | None = None, path: str | None = None) -> int:
    """删除超过保留期的切分文件，返回删除数量；当天的日志文件不动。"""
    path = path or RUN_LOG_PATH
    days = RUN_LOG_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = _utc_day(time.time()) - timedelta(days=days)
    removed = 0
    with _lock:
        _rotate_if_new_day(path)
        for f in log_files(path):
            if f == path:
                continue
            try:
                day = date.fromisoformat(os.path.splitext(f)[0][-10:])
            except ValueError:
                continue
            if day < cutoff:
                os.remove(f)
                removed += 1
    return removed


def _interrupt_info(exc: BaseException) -> dict[str, Any]:
    """从 GraphInterrupt 里取断点类型与涉及字段 key（不含问题文本）。"""
    for intr in exc.args[0] if exc.args else ():
        value = getattr(intr, "value", None)
        pending = value.get("pending") if isinstance(value, dict) else None
        if isinstance(pending, dict):
            return {
                "kind": pending.get("kind"),
                "field_keys": pending.get("field_keys"),
            }
    return {}


def traced_node(
    name: str, fn: Callable[[dict], Awaitable[dict]]
) -> Callable[[dict], Awaitable[dict]]:
    """包装图节点，记录耗时与结局（ok / interrupt / error）；节点本身无需改动。"""

    @functools.wraps(fn)
    async def wrapper(state: dict) -> dict:
        t0 = time.monotonic()
        outcome: dict[str, Any] = {"outcome": "ok"}
        token = _node.set(name)
        try:
            return await fn(state)
        except Exception as exc:  # noqa: BLE001 — 只记录后原样抛出
            if type(exc).__name__ == "GraphInterrupt":
                outcome = {"outcome": "interrupt", **_interrupt_info(exc)}
            else:
                outcome = {
                    "outcome": "error",
                    "error": type(exc).__name__,
                    "error_category": categorize(exc),
                }
            raise
        finally:
            log_event(
                "node",
                node=name,
                duration_ms=round((time.monotonic() - t0) * 1000),
                **outcome,
            )
            _node.reset(token)

    return wrapper


def _has_value(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    v = node.get("value")
    return v not in (None, "") and v != []


def field_summary(fields: dict[str, Any]) -> dict[str, Any]:
    """字段概况：有值/缺失的 key 与来源含 OCR 的 key，不含任何取值。"""
    filled = [k for k, v in fields.items() if _has_value(v)]
    missing = [
        k for k, v in fields.items() if isinstance(v, dict) and not _has_value(v)
    ]
    ocr = [
        k
        for k, v in fields.items()
        if isinstance(v, dict) and "OCR" in str(v.get("src") or "")
    ]
    return {"filled": len(filled), "missing_keys": missing, "ocr_keys": ocr}


def log_lawyer_edits(
    run_id: str,
    delivered: dict[str, Any] | None,
    final_fields: dict[str, Any],
) -> None:
    """
    对比 agent 交付值与律师最终值，只记 key 与改动类型（filled/cleared/modified）。
    delivered 取自该会话 checkpoint 里交给律师时的字段（runner.delivered_fields），
    服务重启后仍在；会话已过期 / 不存在时为 None，记一条 lawyer_edits_unavailable，不静默丢失。
    """
    if delivered is None:
        log_event("lawyer_edits_unavailable", run_id=run_id)
        return
    before = {k: v.get("value") for k, v in delivered.items() if isinstance(v, dict)}
    edits: dict[str, str] = {}
    for key, node in final_fields.items():
        if not isinstance(node, dict) or key not in before:
            continue
        old, new = before[key], node.get("value")
        if old == new or (old in (None, "") and new in (None, "")):
            continue
        edits[key] = (
            "filled"
            if old in (None, "")
            else "cleared"
            if new in (None, "")
            else "modified"
        )
    log_event("lawyer_edits", run_id=run_id, edited=len(edits), fields=edits)
