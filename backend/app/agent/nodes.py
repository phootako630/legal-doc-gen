# Agent 节点：清点 → 抽取 → 校验（含冲突 interrupt）。框架无关工具由 services/ 提供。
#
# 节点为 async（含 LLM 调用），返回对 GraphState 的部分更新。
# 校验节点在发现确定性冲突时通过 LangGraph interrupt 暂停，把决策抛给律师；
# 律师 resume 后节点从头重跑，interrupt() 直接返回决定值，据此改字段再复核。
from __future__ import annotations

import copy
import json

from langgraph.types import interrupt

from app.agent.state import GraphState
from app.config import AGENT_MAX_OCR_PAGES
from app.services import file_store, llm_progress
from app.services.doc_search import CLAUSE_KEYWORDS, locate_clauses
from app.services.extraction import (
    build_combined_text,
    enrich_provenance,
    format_checks_for_llm,
    mark_ocr_fields,
)
from app.services.llm_client import chat
from app.services.ocr_engine import ocr_page
from app.services.prompt_loader import load_prompt
from app.services.validators import ValidationCheck, check_to_dict, run_all_checks

# 三类条款字段（按需 OCR 的目标）：indicator 为“该条款是否已拿到”的判定字段
_CLAUSE_INDICATOR = {
    "payment": "payment_clause_text",
    "breach": "breach_interest_rate_text",
    "dispute": "dispute_clause_text",
}
_ALL_CLAUSE_KEYS = [
    "payment_clause_location",
    "payment_clause_text",
    "breach_interest_clause_location",
    "breach_interest_rate_text",
    "dispute_clause_location",
    "dispute_clause_text",
]

# 起诉状就绪度关注的关键字段（齐全且非冲突才计入）
_READINESS_KEYS = [
    "plaintiff_name_final",
    "defendant_name",
    "contract_no",
    "contract_sign_date",
    "elevator_qty",
    "total_amount",
    "paid_amount",
    "unpaid_amount",
    "acceptance_latest_date",
]


def _field_value(fields: dict, key: str) -> object:
    node = fields.get(key)
    return node.get("value") if isinstance(node, dict) else node


def _conflict_keys(checks: list[ValidationCheck]) -> set[str]:
    keys: set[str] = set()
    for c in checks:
        if c.is_conflict:
            keys.update(c.related_fields)
            if c.key == "qty_consistency":
                keys.add("elevator_qty")
    return keys


def _readiness(fields: dict, checks: list[ValidationCheck]) -> int:
    """起诉状就绪度 0–100：关键字段齐全且非冲突的占比。"""
    conflicted = _conflict_keys(checks)
    good = 0
    for k in _READINESS_KEYS:
        val = _field_value(fields, k)
        present = val is not None and not (isinstance(val, str) and val.strip() == "")
        if present and k not in conflicted:
            good += 1
    return round(good / len(_READINESS_KEYS) * 100)


def _build_conflict_pending(conflicts: list[ValidationCheck]) -> dict:
    """把确定性冲突汇成一个待决断点（field_keys 去重保序）。"""
    field_keys: list[str] = []
    msgs: list[str] = []
    for c in conflicts:
        related = list(c.related_fields)
        if c.key == "qty_consistency":
            related.append("elevator_qty")
        field_keys.extend(related)
        msgs.append(c.message)
    seen: set[str] = set()
    deduped = [k for k in field_keys if not (k in seen or seen.add(k))]
    return {
        "kind": "conflict",
        "question": "检测到数据冲突，请律师核实并确定取值：\n" + "；".join(msgs),
        "options": [],
        "field_keys": deduped,
    }


def _apply_decisions(fields: dict, decisions: dict) -> dict:
    """把律师在断点处给出的取值写回字段（src 标记为人工确认）。"""
    for key, value in (decisions or {}).items():
        node = fields.get(key)
        if isinstance(node, dict):
            node["value"] = value
            node["src"] = "律师确认修改"
        else:
            fields[key] = {"value": value, "src": "律师确认修改"}
    return fields


async def _validate_prose(
    fields: dict, checks: list[ValidationCheck]
) -> tuple[str, str]:
    """据确定性结论调用降级版 validate prompt，仅生成给律师看的说明文本。"""
    prompt = load_prompt(
        "prompt-a-validate.md",
        {
            "extracted_json": json.dumps(fields, ensure_ascii=False),
            "deterministic_checks": format_checks_for_llm(checks),
        },
    )
    raw: str = await chat([{"role": "user", "content": prompt}])
    parts = raw.split("---HIGHLIGHT---", 1)
    report = parts[0].strip()
    highlight = parts[1].strip() if len(parts) > 1 else ""
    return report, highlight


# ── 节点 ──────────────────────────────────────────────────────────────────────
async def checklist_node(state: GraphState) -> dict:
    """材料清点：LLM 判断三类材料是否齐全；不足则设 pending（missing）并终止。"""
    llm_progress.start_stage("材料清点", 1)
    combined = build_combined_text(state["files"])
    prompt = load_prompt("prompt-a-checklist.md", {"files_text": combined})
    checklist: dict = await chat(  # type: ignore[assignment]
        [{"role": "user", "content": prompt}], json_mode=True
    )
    update: dict = {"checklist": checklist}
    if not checklist.get("can_proceed", True):
        missing = checklist.get("missing", [])
        notes = checklist.get("notes", "")
        q = f"材料不足，无法继续处理。缺少：{'、'.join(missing) if missing else '未知'}"
        if notes:
            q += f"。备注：{notes}"
        update["pending"] = {
            "kind": "missing",
            "question": q,
            "options": [],
            "field_keys": [],
        }
    return update


async def extract_node(state: GraphState) -> dict:
    """字段抽取：结构化输出 + 代码层补全 OCR 标记。"""
    llm_progress.start_stage("字段抽取", 2)
    combined = build_combined_text(state["files"])
    checklist_summary = json.dumps(
        state.get("checklist", {}), ensure_ascii=False, indent=2
    )
    prompt = load_prompt(
        "prompt-a-extract.md",
        {
            "files_text": combined,
            "material_checklist": checklist_summary,
            "internet_allowed": str(state.get("internet_allowed", True)),
        },
    )
    fields: dict = await chat(  # type: ignore[assignment]
        [{"role": "user", "content": prompt}], json_mode=True
    )
    mark_ocr_fields(fields, set(state.get("scanned_filenames", [])))
    # 值回原文逐页锚定：补充已验证页码 + 命中片段 + 通道 + confidence（出处可追溯）
    enrich_provenance(fields, state.get("files", []))
    return {"extracted_fields": fields}


async def validate_node(state: GraphState) -> dict:
    """确定性校验：有冲突则 interrupt 问律师，resume 后应用决定并复核。"""
    fields = copy.deepcopy(state["extracted_fields"])
    checks = run_all_checks(fields)
    conflicts = [c for c in checks if c.is_conflict]

    if conflicts:
        payload = {
            "pending": _build_conflict_pending(conflicts),
            "validations": [check_to_dict(c) for c in checks],
            "readiness": _readiness(fields, checks),
        }
        # 暂停，把决策抛给前端；resume 时返回 {field_key: value}
        decisions = interrupt(payload)
        fields = _apply_decisions(fields, decisions or {})
        checks = run_all_checks(fields)

    # 冲突消解（若有）之后才进入 LLM 说明文本生成，进度对齐"校验高亮"阶段
    llm_progress.start_stage("校验高亮", 3)
    report, highlight = await _validate_prose(fields, checks)
    return {
        "extracted_fields": fields,
        "validations": [check_to_dict(c) for c in checks],
        "validation_report": report,
        "highlight_list": highlight,
        "readiness": _readiness(fields, checks),
        "pending": None,
    }


def _is_missing(fields: dict, key: str) -> bool:
    """字段是否缺失（value 为 None 或空串）。"""
    node = fields.get(key)
    val = node.get("value") if isinstance(node, dict) else node
    return val is None or (isinstance(val, str) and val.strip() == "")


async def _extract_clauses(file: dict) -> dict:
    """据某扫描合同已 OCR 的逐页文本，定向抽取 6 个条款字段（结构化 JSON）。"""
    text = "\n\n".join(
        f"【第{p['page']}页】\n{p['text']}"
        for p in file.get("pages", [])
        if p.get("text")
    )
    prompt = load_prompt(
        "prompt-a-clauses.md",
        {"contract_filename": file.get("filename", "合同"), "contract_text": text},
    )
    result = await chat([{"role": "user", "content": prompt}], json_mode=True)
    return result if isinstance(result, dict) else {}


async def ocr_augment_node(state: GraphState) -> dict:
    """
    按需 OCR：仅当付款/违约/争议条款字段仍缺失、且存在暂存了字节的扫描合同时，
    对该合同逐页 OCR（命中三条款即停，页数受 AGENT_MAX_OCR_PAGES 约束），
    再据 OCR 文本定向补齐条款字段并重新锚定页码。OCR 不可用则优雅降级、条款保持缺失。
    """
    fields = state.get("extracted_fields", {})
    missing = {c for c, key in _CLAUSE_INDICATOR.items() if _is_missing(fields, key)}
    if not missing:
        return {}

    contracts = [
        f
        for f in state.get("files", [])
        if f.get("is_scanned")
        and f.get("identified_type") == "合同"
        and f.get("file_id")
    ]
    if not contracts:
        return {}

    fields = copy.deepcopy(fields)
    files = copy.deepcopy(state.get("files", []))
    # 对深拷贝后的同名文件对象操作（保证返回的 files 带上 OCR 结果）
    contracts = [
        f
        for f in files
        if f.get("is_scanned")
        and f.get("identified_type") == "合同"
        and f.get("file_id")
    ]

    budget = AGENT_MAX_OCR_PAGES
    did_ocr = False

    for f in contracts:
        if budget <= 0 or not missing:
            break
        pdf = file_store.get(f.get("file_id"))
        if pdf is None:
            continue  # 字节已淘汰/丢失 → 跳过（条款保持缺失）

        ocr_pages: list[dict] = []
        for page_num in range(1, f.get("page_count", 0) + 1):
            if budget <= 0:
                break
            try:
                page_text = await ocr_page(pdf, page_num)
            except (RuntimeError, TimeoutError):
                break  # OCR 不可用/超时 → 停止，优雅降级
            budget -= 1
            did_ocr = True
            ocr_pages.append({"page": page_num, "text": page_text})
            # 命中即停：所有仍缺失的条款都已在已 OCR 页里定位到，则无需再往下翻
            located = locate_clauses(
                ocr_pages, {c: CLAUSE_KEYWORDS[c] for c in missing}
            )
            if missing <= located:
                break

        if not ocr_pages:
            continue
        # 回填该合同的逐页文本，供 provenance 定位页码 + 后续展示
        f["pages"] = ocr_pages
        f["text"] = "\n\n".join(p["text"] for p in ocr_pages if p["text"])

        clause_fields = await _extract_clauses(f)
        for key in _ALL_CLAUSE_KEYS:
            if _is_missing(fields, key):
                node = clause_fields.get(key)
                if isinstance(node, dict) and not _is_missing({key: node}, key):
                    fields[key] = node
        missing = {c for c in missing if _is_missing(fields, _CLAUSE_INDICATOR[c])}

    if not did_ocr:
        return {}
    # 有新 OCR 文本：重新逐页锚定，令条款字段拿到真实页码 + channel=ocr
    enrich_provenance(fields, files)
    return {"extracted_fields": fields, "files": files}


def after_checklist(state: GraphState) -> str:
    """清点后条件跳转：材料不足（pending）→ 终止；否则进入抽取。"""
    return "end" if state.get("pending") else "extract"
