# Agent 运行封装：启动/恢复图，把图状态或 interrupt 收敛成对外的 CaseState。
#
# run_id 即 LangGraph 的 thread_id；in-memory checkpointer 按此隔离每次会话。
from __future__ import annotations

import time
import uuid

from langgraph.types import Command

from app.agent.graph import get_graph
from app.agent.state import CaseState, PendingDecision
from app.services import run_log


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _interrupt_payload(snapshot) -> dict | None:
    """从快照的待执行任务里取出 interrupt 抛出的 payload（无则 None）。"""
    for task in snapshot.tasks:
        for intr in task.interrupts or []:
            return intr.value
    return None


def _case_from_values(run_id: str, values: dict) -> CaseState:
    pending = values.get("pending")
    return CaseState(
        run_id=run_id,
        extracted_fields=values.get("extracted_fields") or {},
        validations=values.get("validations") or [],
        validation_report=values.get("validation_report") or "",
        highlight_list=values.get("highlight_list") or "",
        readiness=values.get("readiness") or 0,
        pending=PendingDecision(**pending) if pending else None,
    )


def _case_from_interrupt(run_id: str, values: dict, payload: dict) -> CaseState:
    # interrupt 发生在校验节点内部，节点未返回，故 validations/readiness 取自 payload
    return CaseState(
        run_id=run_id,
        extracted_fields=values.get("extracted_fields") or {},
        validations=payload.get("validations") or [],
        readiness=payload.get("readiness") or 0,
        pending=PendingDecision(**payload["pending"]),
    )


async def _collect(run_id: str, config: dict) -> CaseState:
    graph = get_graph()
    snapshot = await graph.aget_state(config)
    payload = _interrupt_payload(snapshot)
    if payload:
        case = _case_from_interrupt(run_id, snapshot.values, payload)
    else:
        case = _case_from_values(run_id, snapshot.values)
    # 记下交付给律师的字段取值，generate 时据此统计律师改动（只存内存，不落盘）
    run_log.remember_fields(run_id, case.extracted_fields)
    run_log.log_event(
        "run_state",
        run_id=run_id,
        pending_kind=case.pending.kind if case.pending else None,
        readiness=case.readiness,
        conflicts=sum(1 for v in case.validations if v.get("is_conflict")),
        **run_log.field_summary(case.extracted_fields),
    )
    return case


async def run_analyze(files: list[dict], internet_allowed: bool) -> CaseState:
    """启动一次 agent 会话，返回结果 CaseState（可能带 pending 断点）。"""
    run_id = uuid.uuid4().hex
    config = _config(run_id)
    run_log.set_run_id(run_id)
    run_log.log_event(
        "run_start",
        files=len(files),
        scanned=sum(1 for f in files if f.get("is_scanned")),
        internet_allowed=internet_allowed,
    )
    initial = {
        "files": files,
        "internet_allowed": internet_allowed,
        "scanned_filenames": [f["filename"] for f in files if f.get("is_scanned")],
        "pending": None,
    }
    t0 = time.monotonic()
    await get_graph().ainvoke(initial, config)
    run_log.log_event("run_pass", duration_ms=round((time.monotonic() - t0) * 1000))
    return await _collect(run_id, config)


async def run_resume(run_id: str, decisions: dict) -> CaseState:
    """在断点处提交律师决定并恢复 agent。run_id 未知则抛 KeyError。"""
    config = _config(run_id)
    snapshot = await get_graph().aget_state(config)
    if not snapshot.tasks and not snapshot.values:
        raise KeyError(run_id)  # 无此会话（进程重启或 run_id 失效）
    run_log.set_run_id(run_id)
    # 只记律师本次决定了哪些字段，不记取值
    run_log.log_event("resume", decided_keys=sorted(decisions))
    t0 = time.monotonic()
    await get_graph().ainvoke(Command(resume=decisions), config)
    run_log.log_event("run_pass", duration_ms=round((time.monotonic() - t0) * 1000))
    return await _collect(run_id, config)
