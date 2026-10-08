# Agent 运行封装：启动/恢复图，把图状态或 interrupt 收敛成对外的 CaseState。
#
# run_id 即 LangGraph 的 thread_id；SQLite checkpointer 按此隔离每次会话。
# run_analyze_once 在其上加幂等：同一请求编号（前端每次上传生成一个）只真正分析一次。
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

from langgraph.types import Command

from app.agent import checkpoint
from app.agent.graph import get_graph
from app.agent.state import CaseState, PendingDecision
from app.services import file_store, llm_progress, run_log


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
    graph = await get_graph()
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


# 保留期是对客户机密的承诺，不能依赖"恰好有新案件进来"才清理：
# 由服务启动时起的后台循环定时执行（见 main.py lifespan），resume 时也会单独拒绝过期会话。
_PURGE_INTERVAL_S = 3600


async def purge_expired_cases() -> None:
    """清除过期的 checkpoint 与上传文件。清理失败只记日志，不影响服务。"""
    try:
        runs = await checkpoint.purge_expired()
        files = await asyncio.to_thread(file_store.purge_expired)
        run_log.log_event("purge", runs=runs, files=files)
    except Exception as exc:  # noqa: BLE001 — 清理是尽力而为
        run_log.log_event("purge_error", error=type(exc).__name__)


async def retention_loop(interval_s: float = _PURGE_INTERVAL_S) -> None:
    """启动时立即清理一次，之后每隔 interval_s 清理一次；随服务关闭被取消。"""
    while True:
        await purge_expired_cases()
        await asyncio.sleep(interval_s)


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
    await checkpoint.record_run(run_id)
    graph = await get_graph()
    await graph.ainvoke(initial, config)
    run_log.log_event("run_pass", duration_ms=round((time.monotonic() - t0) * 1000))
    return await _collect(run_id, config)


async def run_resume(run_id: str, decisions: dict) -> CaseState:
    """在断点处提交律师决定并恢复 agent。run_id 未知则抛 KeyError。"""
    config = _config(run_id)
    graph = await get_graph()
    if await checkpoint.is_expired(run_id):
        await purge_expired_cases()  # 过期会话当场清除，再按"会话不存在"处理
        raise KeyError(run_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.tasks and not snapshot.values:
        raise KeyError(run_id)  # 无此会话（进程重启或 run_id 失效）
    run_log.set_run_id(run_id)
    # 只记律师本次决定了哪些字段，不记取值
    run_log.log_event("resume", decided_keys=sorted(decisions))
    t0 = time.monotonic()
    await graph.ainvoke(Command(resume=decisions), config)
    run_log.log_event("run_pass", duration_ms=round((time.monotonic() - t0) * 1000))
    return await _collect(run_id, config)


# ── 幂等：同一请求编号只分析一次 ──────────────────────────────────────────────
#
# 一次分析要 2–3 分钟（OCR + 多次 LLM）。请求中途断开（网络抖动、反向代理超时）时，
# 后端其实还在跑；律师点「重试」若再起一次完整分析，就是双倍耗时和费用，前一次结果也白费。
# 前端每次上传生成一个请求编号（Idempotency-Key），同一编号的请求：
#   1. 正在分析 → 不另起，等同一个任务的结果（_inflight，进程内）；
#   2. 已经分析完 → 直接返回该会话的当前状态（analyze_keys 表，落盘，重启后仍有效）；
#   3. 分析失败 → 不记编号，重试会重新分析；
#   4. 编号相同但材料不同 → 拒绝（IdempotencyConflict），避免把 A 案的结果当成 B 案返回。


class IdempotencyConflict(Exception):
    """同一请求编号被用于另一组材料。"""


# 请求编号 → (请求摘要, 正在运行的分析任务)。单进程部署，进程内字典即可
_inflight: dict[str, tuple[str, asyncio.Task[CaseState]]] = {}


def request_fingerprint(files: list[dict], internet_allowed: bool) -> str:
    """请求内容的摘要：材料（含全文）与联网开关任一不同，摘要就不同。"""
    raw = json.dumps(
        {"files": files, "internet_allowed": internet_allowed},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _running_task(key: str, fingerprint: str) -> asyncio.Task[CaseState] | None:
    """同编号正在运行（或刚成功结束、还没从字典移除）的任务；编号对应别的材料则报冲突。"""
    entry = _inflight.get(key)
    if entry is None:
        return None
    entry_fp, task = entry
    if entry_fp != fingerprint:
        raise IdempotencyConflict(key)
    if task.done() and (task.cancelled() or task.exception() is not None):
        return None  # 失败的任务不复用，让这次请求重新分析
    return task


async def _existing_case(run_id: str) -> CaseState | None:
    """已有会话的当前状态（含律师之后在断点处的决定）；会话已过期或不存在则 None。"""
    if await checkpoint.is_expired(run_id):
        return None
    graph = await get_graph()
    config = _config(run_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.tasks and not snapshot.values:
        return None
    return await _collect(run_id, config)


async def _analyze_and_remember(
    key: str, fingerprint: str, files: list[dict], internet_allowed: bool
) -> CaseState:
    """
    真正跑一次分析；成功后记下编号。进度以请求编号为 id、跟着任务走：
    重复请求接上同一个任务时共用这条进度，不会把进度条重置，也不会串到别人的分析。
    """
    with llm_progress.track(key, total_stages=3):
        case = await run_analyze(files, internet_allowed)
    await checkpoint.save_analyze_key(key, fingerprint, case.run_id)
    return case


async def run_analyze_once(
    key: str, files: list[dict], internet_allowed: bool
) -> CaseState:
    """带请求编号的分析：重复请求复用正在运行的任务或已完成的会话，不重复消耗 OCR / LLM。"""
    fingerprint = request_fingerprint(files, internet_allowed)

    task = _running_task(key, fingerprint)
    if task is None:
        saved = await checkpoint.find_analyze_key(key)
        if saved is not None:
            saved_fp, run_id = saved
            if saved_fp != fingerprint:
                raise IdempotencyConflict(key)
            case = await _existing_case(run_id)
            if case is not None:
                run_log.log_event("analyze_replay", run_id=run_id)
                return case
            await checkpoint.forget_analyze_key(key)  # 会话已清除：按新请求处理
        # 上面有 await，期间同编号的另一个请求可能已经起了任务：再查一次。
        # 从这里到登记进 _inflight 之间没有 await，不会再被插队（asyncio 单线程）
        task = _running_task(key, fingerprint)

    if task is None:
        task = asyncio.create_task(
            _analyze_and_remember(key, fingerprint, files, internet_allowed)
        )
        _inflight[key] = (fingerprint, task)

        def _forget(done: asyncio.Task[CaseState], key: str = key) -> None:
            if _inflight.get(key, (None, None))[1] is done:
                _inflight.pop(key, None)

        task.add_done_callback(_forget)
    else:
        run_log.log_event("analyze_join")  # 重复请求：接上正在运行的分析

    # shield：本次 HTTP 请求被取消（客户端断开）时只放弃等待，不取消分析本身，
    # 后续同编号的重试还能接上这次的结果
    return await asyncio.shield(task)
