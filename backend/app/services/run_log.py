# 运行日志：agent 每次运行的结构化事件，追加写入本地 JSONL，供复盘耗时/成本/律师改动率
#
# 保密约束：案件材料是客户机密，日志只记字段 key、数量、耗时、token，绝不记字段取值或原文。
# 日志写入失败一律吞掉——可观测性不能影响主流程。
from __future__ import annotations

import functools
import json
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config import RUN_LOG_ENABLED, RUN_LOG_PATH

# 当前协程所属的 run_id；runner 入口设置，节点/LLM 调用无需逐层传参
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_lock = threading.Lock()
# 每个 run 最近一次交给律师的字段值（仅内存，用来在 generate 时算律师改动），上限防泄漏
_last_fields: dict[str, dict[str, Any]] = {}
_LAST_FIELDS_CAP = 50


def set_run_id(run_id: str | None) -> None:
    _run_id.set(run_id)


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
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except (OSError, TypeError, ValueError):
        pass


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
        try:
            return await fn(state)
        except Exception as exc:  # noqa: BLE001 — 只记录后原样抛出
            if type(exc).__name__ == "GraphInterrupt":
                outcome = {"outcome": "interrupt", **_interrupt_info(exc)}
            else:
                outcome = {"outcome": "error", "error": type(exc).__name__}
            raise
        finally:
            log_event(
                "node",
                node=name,
                duration_ms=round((time.monotonic() - t0) * 1000),
                **outcome,
            )

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


def remember_fields(run_id: str, fields: dict[str, Any]) -> None:
    """记下本次交给律师的字段取值，供 generate 时对比。"""
    snap = {k: v.get("value") for k, v in fields.items() if isinstance(v, dict)}
    with _lock:
        _last_fields[run_id] = snap
        while len(_last_fields) > _LAST_FIELDS_CAP:
            _last_fields.pop(next(iter(_last_fields)))


def log_lawyer_edits(run_id: str, final_fields: dict[str, Any]) -> None:
    """对比 agent 交付值与律师最终值，只记 key 与改动类型（filled/cleared/modified）。"""
    with _lock:
        before = _last_fields.get(run_id)
    if before is None:
        return
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
