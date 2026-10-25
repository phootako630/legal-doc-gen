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
from datetime import datetime, timezone

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


def _waited_s(snapshot) -> float | None:
    """断点快照写入（即暂停）距今的秒数；时间读不出时为 None。"""
    try:
        paused = datetime.fromisoformat(snapshot.created_at)
    except (TypeError, ValueError):
        return None
    return round((datetime.now(timezone.utc) - paused).total_seconds(), 1)


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
        await _log_abandoned()
    except Exception as exc:  # noqa: BLE001 — 统计失败不能挡住保留期清理
        run_log.log_event("abandoned_scan_error", error=type(exc).__name__)
    try:
        runs = await checkpoint.purge_expired()
        files = await asyncio.to_thread(file_store.purge_expired)
        logs = await asyncio.to_thread(run_log.purge_old)
        run_log.log_event("purge", runs=runs, files=files, logs=logs)
    except Exception as exc:  # noqa: BLE001 — 清理是尽力而为
        run_log.log_event("purge_error", error=type(exc).__name__)


async def _log_abandoned() -> None:
    """过期时仍停在断点、律师一直没作答的会话记 abandoned（统计放弃率）；只记断点类型。
    单个会话读不出状态时跳过它（记一条 abandoned_scan_error），不影响其余会话和随后的清理。"""
    graph = await get_graph()
    for run_id in await checkpoint.expired_run_ids():
        try:
            payload = _interrupt_payload(await graph.aget_state(_config(run_id)))
        except Exception as exc:  # noqa: BLE001
            run_log.log_event("abandoned_scan_error", run_id=run_id, error=type(exc).__name__)
            continue
        if payload:
            pending = payload.get("pending") if isinstance(payload, dict) else None
            kind = pending.get("kind") if isinstance(pending, dict) else None
            run_log.log_event("abandoned", run_id=run_id, pending_kind=kind)


async def retention_loop(interval_s: float = _PURGE_INTERVAL_S) -> None:
    """启动时立即清理一次，之后每隔 interval_s 清理一次；随服务关闭被取消。"""
    while True:
        await purge_expired_cases()
        await asyncio.sleep(interval_s)


# ── 部署排空 ──────────────────────────────────────────────────────────────────
# 部署脚本重启前先 POST /drain：此后不再接新的分析 / 断点恢复（503「系统正在更新」），
# 再轮询 GET /busy 等进行中的归零才重启。先关入口再等归零，避免「读到 0 后又有人开始分析、
# 随即被重启打断」（#86 评审）。重启后是新进程，自然恢复接单。
_active_runs = 0
_draining = False


class Draining(Exception):
    """服务准备重启，暂不接新的分析 / 断点恢复。"""


def active_runs() -> int:
    return _active_runs


def is_draining() -> bool:
    return _draining


def start_draining() -> None:
    global _draining
    _draining = True


def _begin_run() -> None:
    """登记一个运行；排空中拒绝。与 _end_run 成对。"""
    global _active_runs
    if _draining:
        raise Draining()
    _active_runs += 1


def _end_run() -> None:
    global _active_runs
    _active_runs -= 1


async def run_analyze(
    files: list[dict], internet_allowed: bool, owner: str | None = None
) -> CaseState:
    """启动一次 agent 会话，返回结果 CaseState（可能带 pending 断点）。owner 为发起的登录账号。"""
    _begin_run()
    try:
        return await _run_analyze(files, internet_allowed, owner)
    finally:
        _end_run()


async def _run_analyze(
    files: list[dict], internet_allowed: bool, owner: str | None = None
) -> CaseState:
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
    await checkpoint.record_run(run_id, owner)
    graph = await get_graph()
    await graph.ainvoke(initial, config)
    run_log.log_event("run_pass", duration_ms=round((time.monotonic() - t0) * 1000))
    return await _collect(run_id, config)


async def run_resume(run_id: str, decisions: dict, user: str | None = None) -> CaseState:
    """
    在断点处提交律师决定并恢复 agent。run_id 未知，或不是该账号发起的会话，抛 KeyError
    （按「不存在」处理，不透露别人的会话是否存在）。
    """
    if not await checkpoint.can_access(run_id, user):
        raise KeyError(run_id)
    _begin_run()
    try:
        return await _run_resume(run_id, decisions)
    finally:
        _end_run()


async def _run_resume(run_id: str, decisions: dict) -> CaseState:
    config = _config(run_id)
    graph = await get_graph()
    if await checkpoint.is_expired(run_id):
        await purge_expired_cases()  # 过期会话当场清除，再按"会话不存在"处理
        raise KeyError(run_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.tasks and not snapshot.values:
        raise KeyError(run_id)  # 无此会话（进程重启或 run_id 失效）
    run_log.set_run_id(run_id)
    # 只记律师本次决定了哪些字段，不记取值；wait_s = 停在断点到律师作答的秒数（#75）
    run_log.log_event("resume", decided_keys=sorted(decisions), wait_s=_waited_s(snapshot))
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


# 请求编号 → (请求摘要, 正在运行的分析任务, 发起账号)。单进程部署，进程内字典即可
_inflight: dict[str, tuple[str, asyncio.Task[CaseState], str | None]] = {}


def request_fingerprint(
    files: list[dict], internet_allowed: bool, owner: str | None = None
) -> str:
    """请求内容的摘要：材料（含全文）、联网开关与发起账号任一不同，摘要就不同。"""
    raw = json.dumps(
        {"files": files, "internet_allowed": internet_allowed, "owner": owner},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _legacy_fingerprint(files: list[dict], internet_allowed: bool) -> str:
    """按账号隔离之前的摘要格式（不含账号）：升级前已落盘的请求编号用它比对（#93 评审）。"""
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
    entry_fp, task, _owner = entry
    if entry_fp != fingerprint:
        raise IdempotencyConflict(key)
    if task.done() and (task.cancelled() or task.exception() is not None):
        return None  # 失败的任务不复用，让这次请求重新分析
    return task


async def get_case(run_id: str, user: str | None = None) -> CaseState | None:
    """
    已有会话的当前状态（含律师之后在断点处的决定）；会话已过期、不存在或不是该账号发起的则 None。
    幂等重放与前端刷新后「继续上次的案件」共用。
    """
    if await checkpoint.is_expired(run_id) or not await checkpoint.can_access(run_id, user):
        return None
    graph = await get_graph()
    config = _config(run_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.tasks and not snapshot.values:
        return None
    return await _collect(run_id, config)


async def delivered_fields(run_id: str, user: str | None = None) -> dict | None:
    """
    交给律师审核时的字段（checkpoint 里的 extracted_fields；律师在断点处的决定已并入），
    generate 时据此统计律师在审核页的改动。会话过期 / 不存在则 None。
    落在 checkpoint 里，服务重启后仍可对比，随会话一起过期清除。不是该账号的会话也返回 None。
    """
    if await checkpoint.is_expired(run_id) or not await checkpoint.can_access(run_id, user):
        return None
    graph = await get_graph()
    snapshot = await graph.aget_state(_config(run_id))
    fields = (snapshot.values or {}).get("extracted_fields")
    return fields if isinstance(fields, dict) else None


async def _analyze_and_remember(
    key: str,
    fingerprint: str,
    files: list[dict],
    internet_allowed: bool,
    owner: str | None = None,
) -> CaseState:
    """
    真正跑一次分析；成功后记下编号。进度以请求编号为 id、跟着任务走：
    重复请求接上同一个任务时共用这条进度，不会把进度条重置，也不会串到别人的分析。
    """
    with llm_progress.track(key, total_stages=3):
        case = await _run_analyze(files, internet_allowed, owner)
    # 运行计数要覆盖到这里：编号落盘前重启，刷新后的页面就找不回这次分析（#86 评审）
    await checkpoint.save_analyze_key(key, fingerprint, case.run_id)
    return case


async def analyze_result(key: str, user: str | None = None) -> CaseState | None:
    """
    按请求编号取分析结果（前端在处理页刷新后接回）：还在跑就等它跑完，已跑完返回会话当前状态；
    没有这次分析（失败、服务重启、已过期）或不是该账号发起的，返回 None。
    不比对材料：编号是前端随机生成的，与 run_id 一样只有发起的标签页知道。
    """
    entry = _inflight.get(key)
    if entry is not None:
        if user is not None and entry[2] is not None and entry[2] != user:
            return None
        try:
            return await asyncio.shield(entry[1])  # 同 run_analyze_once：请求断开不取消分析
        except Exception:  # noqa: BLE001 — 接上的分析失败：与「找不到」同样处理（404，重新上传）
            return None
    saved = await checkpoint.find_analyze_key(key)
    return await get_case(saved[1], user) if saved is not None else None


async def run_analyze_once(
    key: str, files: list[dict], internet_allowed: bool, owner: str | None = None
) -> CaseState:
    """
    带请求编号的分析：重复请求复用正在运行的任务或已完成的会话，不重复消耗 OCR / LLM。
    发起账号算在请求摘要里：别的账号用同一编号按「编号对应别的请求」拒绝，接不上别人的分析。
    """
    fingerprint = request_fingerprint(files, internet_allowed, owner)

    task = _running_task(key, fingerprint)
    if task is None:
        saved = await checkpoint.find_analyze_key(key)
        if saved is not None:
            saved_fp, run_id = saved
            # 升级前落盘的编号用旧摘要（不含账号）：材料一致、会话也允许当前账号访问（旧会话没记账号）
            # 时照常接回，否则处理页「重试」会一直 409
            legacy_ok = saved_fp == _legacy_fingerprint(
                files, internet_allowed
            ) and await checkpoint.can_access(run_id, owner)
            if saved_fp != fingerprint and not legacy_ok:
                raise IdempotencyConflict(key)
            case = await get_case(run_id, owner)
            if case is not None:
                run_log.log_event("analyze_replay", run_id=run_id)
                return case
            await checkpoint.forget_analyze_key(key)  # 会话已清除：按新请求处理
        # 上面有 await，期间同编号的另一个请求可能已经起了任务：再查一次。
        # 从这里到登记进 _inflight 之间没有 await，不会再被插队（asyncio 单线程）
        task = _running_task(key, fingerprint)

    if task is None:
        # 建任务前同步登记（排空中直接拒绝），任务结束（含编号落盘）才注销：中间没有 await，
        # /busy 不会在任务刚建好、还没开始跑时读到 0
        _begin_run()
        task = asyncio.create_task(
            _analyze_and_remember(key, fingerprint, files, internet_allowed, owner)
        )
        _inflight[key] = (fingerprint, task, owner)

        def _forget(done: asyncio.Task[CaseState], key: str = key) -> None:
            _end_run()
            if _inflight.get(key, (None, None, None))[1] is done:
                _inflight.pop(key, None)

        task.add_done_callback(_forget)
    else:
        run_log.log_event("analyze_join")  # 重复请求：接上正在运行的分析

    # shield：本次 HTTP 请求被取消（客户端断开）时只放弃等待，不取消分析本身，
    # 后续同编号的重试还能接上这次的结果
    return await asyncio.shield(task)
