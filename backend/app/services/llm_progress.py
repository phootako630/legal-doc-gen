# LLM 处理进度的内存态记录，供前端在 /api/extract 处理期间轮询展示真实阶段
#
# 与 upload_progress 同一套思路：v1 单进程、单用户，模块级全局变量即可；
# 将来多用户并发时需改为按任务 ID 隔离。
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()

_state: dict[str, Any] = {
    "active": False,       # 是否有 LLM 任务正在处理
    "stage": "",           # 当前阶段名：'材料清点' | '字段抽取' | '校验高亮'
    "stage_index": 0,      # 当前阶段序号（从 1 开始）
    "total_stages": 0,     # 总阶段数
}
_stage_started: float = 0.0  # 当前阶段开始时刻（monotonic），用于计算已耗时


def begin(total_stages: int) -> None:
    """一次 LLM 任务开始时调用，重置进度。"""
    global _stage_started
    with _lock:
        _state.update(active=True, stage="", stage_index=0, total_stages=total_stages)
        _stage_started = time.monotonic()


def start_stage(name: str, index: int) -> None:
    """进入第 index 个阶段（从 1 开始）。"""
    global _stage_started
    with _lock:
        _state.update(stage=name, stage_index=index)
        _stage_started = time.monotonic()


def finish() -> None:
    """任务结束（无论成败）时调用。"""
    with _lock:
        _state.update(active=False, stage="", stage_index=0)


def snapshot() -> dict[str, Any]:
    """返回当前进度副本，附带当前阶段已耗时（秒），供进度查询接口使用。"""
    with _lock:
        result = dict(_state)
        result["stage_elapsed_s"] = (
            int(time.monotonic() - _stage_started) if _state["active"] else 0
        )
        return result
