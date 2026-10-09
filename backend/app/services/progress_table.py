# 按任务隔离的内存态进度表：上传进度与 LLM 分析进度共用的底层机制
#
# 多人同时使用（UAT：开发者 + 律师）时，进度必须按任务分开，否则两人的进度条会互相串。
# 前端为每次上传/分析生成一个 progress_id 随请求带上；路由用 track() 把它绑定到当前
# 异步上下文，之后的 update() 无需显式传 id——contextvar 会被 LangGraph 派生的异步子任务
# 自动继承（但不会进入线程池，线程里调用 update 需显式传 task_id）。
# 仍是单进程内存态（uvicorn 只能跑 1 个 worker）；多进程部署时需改为 Redis 等共享存储。
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_KEEP_FINISHED_S = 600  # 已结束任务保留 10 分钟，避免迟到的轮询读到“未知任务”
_MAX_TASKS = 200  # 进度表上限，防止长时间运行内存增长


class ProgressTable:
    """一张以 progress_id 为键的进度表。idle() 给出某任务“非活跃”时的字段默认值。"""

    def __init__(self, name: str, idle: Callable[[], dict[str, Any]]) -> None:
        self._idle = idle
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._current: ContextVar[str | None] = ContextVar(f"{name}_task", default=None)

    def _prune(self, now: float) -> None:
        """清理过期的已结束任务（调用方须持锁）。"""
        expired = [
            tid
            for tid, s in self._tasks.items()
            if not s["active"] and now - s["_updated"] > _KEEP_FINISHED_S
        ]
        for tid in expired:
            del self._tasks[tid]
        while len(self._tasks) > _MAX_TASKS:
            del self._tasks[next(iter(self._tasks))]

    def current(self) -> str | None:
        """当前异步上下文绑定的任务 id（未绑定为 None）。"""
        return self._current.get()

    def begin(self, task_id: str, **fields: Any) -> None:
        """任务开始：以 idle 默认值为底、标记 active，并写入初始字段。"""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._tasks[task_id] = {**self._idle(), **fields, "active": True, "_updated": now}

    def update(self, task_id: str | None = None, **fields: Any) -> None:
        """更新任务字段；未给 id 时用当前绑定的任务，都没有（如单测直调）则忽略。"""
        tid = task_id or self._current.get()
        if not tid:
            return
        with self._lock:
            state = self._tasks.get(tid)
            if state is not None:
                state.update(fields, _updated=time.monotonic())

    def increment(self, key: str, task_id: str | None = None) -> None:
        """对某个计数字段 +1（并发安全）。"""
        tid = task_id or self._current.get()
        if not tid:
            return
        with self._lock:
            state = self._tasks.get(tid)
            if state is not None:
                state[key] = state.get(key, 0) + 1
                state["_updated"] = time.monotonic()

    def finish(self, task_id: str) -> None:
        """任务结束（无论成败）：回到 idle 默认值并标记非活跃，保留一段时间供轮询读取。"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id] = {
                    **self._idle(),
                    "active": False,
                    "_updated": time.monotonic(),
                }

    @contextmanager
    def track(self, task_id: str | None, **fields: Any) -> Iterator[None]:
        """
        绑定当前任务并记录始末：with 块内（含派生的异步子任务）的 update 均记到该任务。
        task_id 为空（旧客户端未带 progress_id）时不记录进度，业务照常执行。
        """
        if not task_id:
            yield
            return
        token = self._current.set(task_id)
        self.begin(task_id, **fields)
        try:
            yield
        finally:
            self.finish(task_id)
            self._current.reset(token)

    def snapshot(
        self, task_id: str | None, *, include_internal: bool = False
    ) -> dict[str, Any]:
        """
        返回某任务的进度副本；未知任务返回非活跃默认值。
        下划线开头的内部字段（如时间戳）默认不返回，include_internal=True 时保留。
        """
        with self._lock:
            state = self._tasks.get(task_id) if task_id else None
            if state is None:
                return {**self._idle(), "active": False}
            return {
                k: v for k, v in state.items() if include_internal or not k.startswith("_")
            }
