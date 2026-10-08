# LLM 处理进度（材料清点 / 字段抽取 / 校验高亮），按 progress_id 隔离，供前端轮询各自任务
#
# 路由用 track(progress_id, total_stages) 包住一次分析；agent 节点里调用 start_stage()
# 无需传 id（见 progress_table 的 contextvar 说明）。
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.services.progress_table import ProgressTable


def _idle() -> dict[str, Any]:
    return {"stage": "", "stage_index": 0, "total_stages": 0}


_table = ProgressTable("llm_progress", _idle)


@contextmanager
def track(task_id: str | None, total_stages: int) -> Iterator[None]:
    """包住一次 LLM 任务：with 块内的 start_stage 记到 task_id 名下，结束自动复位。"""
    with _table.track(
        task_id, total_stages=total_stages, _stage_started=time.monotonic()
    ):
        yield


def start_stage(name: str, index: int) -> None:
    """进入第 index 个阶段（从 1 开始）；未绑定任务时不记录。"""
    _table.update(stage=name, stage_index=index, _stage_started=time.monotonic())


def snapshot(task_id: str | None) -> dict[str, Any]:
    """某任务的当前进度，附带当前阶段已耗时（秒）；未知/已结束任务耗时为 0。"""
    raw = _table.snapshot(task_id, include_internal=True)
    started = raw.get("_stage_started")
    snap = {k: v for k, v in raw.items() if not k.startswith("_")}
    snap["stage_elapsed_s"] = (
        int(time.monotonic() - started) if snap["active"] and started else 0
    )
    return snap
