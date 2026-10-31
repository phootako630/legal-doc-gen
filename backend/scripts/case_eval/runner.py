# eval 跑单个案件：与网页同一条链路（parse_file → run_analyze → 断点 run_resume → 渲染起诉状），
# 只是不经过浏览器；断点按 expected.json 的 decisions 作答，再与标准字段逐项比对。
from __future__ import annotations

import io
import json
import os
import time
from contextlib import nullcontext
from datetime import date
from typing import Any
from unittest import mock

from starlette.datastructures import UploadFile

from app.agent.graph import get_graph
from app.agent.runner import run_analyze, run_resume
from app.services import derived_fields
from app.services.complaint_renderer import render_complaint
from app.services.file_parser import parse_file
from scripts.case_eval.cache import ReplayCache, patched
from scripts.case_eval.scoring import score_case, summarize, summarize_causes, summarize_quality

MATERIAL_EXTS = (".pdf", ".docx", ".doc")
MAX_RESUMES = 6  # 断点轮数上限：正常案件 0–2 次，超过说明在原地打转


def load_expected(case_dir: str) -> dict[str, Any]:
    with open(os.path.join(case_dir, "expected.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("fields"), dict) or not data["fields"]:
        raise ValueError("expected.json 缺少 fields")
    return data


def list_materials(case_dir: str) -> list[str]:
    folder = os.path.join(case_dir, "materials")
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(MATERIAL_EXTS))
    if not names:
        raise ValueError("materials/ 下没有 PDF / Word 文件")
    return [os.path.join(folder, n) for n in names]


async def _parse(path: str) -> dict[str, Any]:
    with open(path, "rb") as fh:
        data = fh.read()
    upload = UploadFile(file=io.BytesIO(data), filename=os.path.basename(path))
    return await parse_file(upload)


def _pause_record(pending: Any, answered: dict[str, Any]) -> dict[str, Any]:
    question = (pending.question or "").strip()
    return {
        "kind": pending.kind,
        "field_keys": list(pending.field_keys),
        "question": question.splitlines()[0][:120] if question else "",
        "answered": answered,
        "unanswered": [k for k in pending.field_keys if k not in answered],
    }


async def _final_files(run_id: str) -> list[dict[str, Any]]:
    """分析结束时图状态里的材料：含 OCR 补读出的逐页文字，核对来源要在这里面回原文找。"""
    graph = await get_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
    return list((snapshot.values or {}).get("files") or [])


def _conflict_keys(validations: list[dict[str, Any]]) -> set[str]:
    """审核页按冲突显示的字段（与前端 collectConflictKeys 一致）。"""
    keys: set[str] = set()
    for v in validations:
        if not v.get("is_conflict"):
            continue
        keys.update(v.get("related_fields") or [])
        if v.get("key") == "qty_consistency":
            keys.add("elevator_qty")
    return keys


def _as_of(expected: dict[str, Any]):
    """expected.json 的 as_of（「2025-03-01」）：按这一天当作生成日（质保期是否届满、暂计日等），
    让真实案卷按律师起诉当时判分、虚构案卷不随日子过去而变；没写就是今天。"""
    raw = expected.get("as_of")
    if not raw:
        return nullcontext()
    day = date.fromisoformat(raw)
    return mock.patch.object(derived_fields, "_today", lambda: day)


async def run_case(case_dir: str, mode: str = "replay", strict: bool = False) -> dict[str, Any]:
    """跑一个案件，返回结果（含逐字段结论、断点、调用统计、生成的起诉状）。出错不抛，记在 error。"""
    name = os.path.basename(os.path.normpath(case_dir))
    result: dict[str, Any] = {"case": name, "error": None}
    started = time.monotonic()
    cache = ReplayCache(os.path.join(case_dir, "cache"), mode=mode, strict=strict)
    try:
        expected = load_expected(case_dir)
        result["title"] = expected.get("title") or name
        decisions: dict[str, Any] = expected.get("decisions") or {}
        with patched(cache), _as_of(expected):
            files = [await _parse(p) for p in list_materials(case_dir)]
            result["materials"] = [
                {"filename": f["filename"], "type": f["identified_type"], "scanned": f["is_scanned"]}
                for f in files
            ]
            # 联网查询目前是占位实现；关掉以免将来接入后结果随查询时间变化
            case = await run_analyze(files, internet_allowed=False)
            pauses: list[dict[str, Any]] = []
            while case.pending and len(pauses) < MAX_RESUMES:
                answered = {k: decisions[k] for k in case.pending.field_keys if k in decisions}
                pauses.append(_pause_record(case.pending, answered))
                if not answered:
                    break  # expected.json 没给这个断点的答案：停在断点（记为 stuck），不提交空决定
                case = await run_resume(case.run_id, answered)
        result["pauses"] = pauses
        result["stuck"] = case.pending is not None  # 轮数用完仍在断点
        fields = case.extracted_fields
        result["readiness"] = case.readiness
        result["fields"] = fields
        with _as_of(expected):
            result["complaint"] = render_complaint(fields)
        scored = score_case(
            expected["fields"], fields, await _final_files(case.run_id), _conflict_keys(case.validations)
        )
        result["results"] = [r.as_dict() for r in scored]
        result["summary"] = summarize(scored)
        result["quality"] = summarize_quality(scored)
        result["causes"] = summarize_causes(scored)
    except Exception as exc:  # noqa: BLE001 — 一个案件出错不影响其他案件
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["stats"] = cache.stats.as_dict()
    result["duration_s"] = round(time.monotonic() - started, 1)
    return result
