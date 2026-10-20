# Agent 节点：清点 → 抽取 → 校验（含冲突 interrupt）。框架无关工具由 services/ 提供。
#
# 节点为 async（含 LLM 调用），返回对 GraphState 的部分更新。
# 校验节点在发现确定性冲突时通过 LangGraph interrupt 暂停，把决策抛给律师；
# 律师 resume 后节点从头重跑，interrupt() 直接返回决定值，据此改字段再复核。
from __future__ import annotations

import asyncio
import copy
import json

from langgraph.types import interrupt

from app.agent.state import GraphState
from app.config import (
    AGENT_MAX_OCR_PAGES,
    AGENT_MAX_REPORT_OCR_PAGES,
    OCR_MAX_CONCURRENCY,
)
from app.services import file_store, llm_progress, run_log
from app.services.acceptance_report import (
    count_report_first_pages,
    first_pages_without_date,
    first_install_address,
    latest_approved_date,
)
from app.services.complaint_renderer import fill_status
from app.services.doc_search import locate_clause_bodies
from app.services.equipment_list import count_vge_units
from app.services.extraction import (
    apply_acceptance_count,
    count_acceptance_units,
    STRATEGIC_CLAUSE_KEYS,
    STRATEGIC_LABEL,
    apply_rule_guards,
    apply_strategic_guards,
    build_combined_text,
    enrich_provenance,
    format_checks_for_llm,
    mark_ocr_fields,
    mark_supplement_agreements,
)
from app.services.derived_fields import (
    apply_derived_fields,
    needs_warranty_period,
    parse_percent,
    PAYABLE_FROM_STAGES,
    payable_ratio_field,
    payable_ratio_options,
    retention_is_guarantee,
)
from app.services.llm_client import chat
from app.services.ocr_engine import ocr_page, pdf_page_count
from app.services.prompt_loader import load_prompt
from app.services.validators import (
    ValidationCheck,
    check_to_dict,
    parse_date,
    parse_qty,
    run_all_checks,
)

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
    "breach_clause_text",  # 第三轮第 4 题：约定违约金时引用条款原文
    "dispute_clause_location",
    "dispute_clause_text",
    "delivery_place",  # 第三轮第 9 题：约定交货地法院时取交货地点
    "warranty_period",  # 补充答复 Q6：质保金按日违约金的起算日 = 验收 + 质保期的次日
    "payment_clause_summary",
    "retention_ratio",
    "retention_clause_text",
    "arbitration_institution",
    "defendant_address_ref",  # 合同盖章页甲方地址：被告所在地管辖时推断法院用（新案卷核对）
    "contract_sign_place",  # 合同盖章页「合同签订地」：约定签订地法院时推断法院用
    "contract_sign_place_cover",  # 确认单（五）第 15 题：封面「合同订立地点」，与盖章页不一致时交律师
    "handover_clause_text",  # 确认单（五）第 6 题：约定移交物业才写「移交物业」
    "contract_total_in_contract",  # 确认单（五）第 8 题：有补充协议时写原合同总价
]
# 以合同为准的字段：律师取合同盖章页/封面/协议书（签约日期、合同名称、合同台数、安装地址）。
# OCR 合同后若取得这些值，覆盖此前从审批表/验收报告推测的值。
_CONTRACT_AUTHORITATIVE_KEYS = [
    "contract_sign_date",  # 律师规则：签约日期以合同盖章页为准（确认单第 4 题）
    "contract_title",
    "contract_party_b",  # 确认单（五）第 1 题：安装合同原告以合同盖章页乙方为准
    "contract_party_a",  # 律师反馈：被告名称以合同盖章页甲方 / 买方为准
    "elevator_qty_by_contract",
    "project_site",
]


def _conflict_keys(checks: list[ValidationCheck]) -> set[str]:
    keys: set[str] = set()
    for c in checks:
        if c.is_conflict:
            keys.update(c.related_fields)
            if c.key == "qty_consistency":
                keys.add("elevator_qty")
    return keys


def _readiness(fields: dict, checks: list[ValidationCheck]) -> int:
    """起诉状就绪度 0–100：模板里的填空中，有值且不冲突的占比。

    原先只看 10 个固定字段，原告信用代码 / 负责人 / 住址、被告法定代表人 / 住址等
    缺失时仍显示 100%；改为按模板实际填空统计，与预览页「N 处待补充」口径一致。
    """
    keys, missing = fill_status(fields)
    if not keys:
        return 0
    conflicted = _conflict_keys(checks)
    good = sum(1 for k in keys if k not in missing and k not in conflicted)
    return round(good / len(keys) * 100)


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
    # 验收口径台数由代码数报告里的设备代码，不信 LLM 数数（实测会数错）
    apply_acceptance_count(fields, state.get("files", []))
    # 确认单（五）第 8 题：有补充协议时记下，起诉状加一句并提醒律师
    mark_supplement_agreements(fields, state.get("files", []))
    # 律师规则把关：被告住址只用工商登记信息、AI 归纳的付款条款标待核实
    apply_rule_guards(fields)
    # 战略 / 框架合同只提供条款；主合同争议条款只转引战略合同时，留给按需读取去战略合同找
    apply_strategic_guards(fields, state.get("files", []))
    # 值回原文逐页锚定：补充已验证页码 + 命中片段 + 通道 + confidence（出处可追溯）
    enrich_provenance(fields, state.get("files", []))
    # 锚定可能把出处改成实际命中的战略合同：按实际来源再把关一次（#56 评审）
    apply_strategic_guards(fields, state.get("files", []))
    return {"extracted_fields": fields}


async def validate_node(state: GraphState) -> dict:
    """确定性校验：有冲突则 interrupt 问律师，resume 后应用决定并复核。"""
    fields = copy.deepcopy(state["extracted_fields"])
    # 派生字段（逾期利息标准、管辖法院辖区）进审核页，律师可见可改
    apply_derived_fields(fields)
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
        apply_derived_fields(fields)  # 律师改了工程地点等依赖字段时刷新推定
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


def _clause_ocr_targets(files: list[dict]) -> list[dict]:
    """
    按需读条款的文件：扫描主合同在前，战略 / 框架合同（扫描件或文字版）排在后面——主合同找不全条款，
    或争议条款只写「按战略合同执行」时才读到它（律师反馈：徐州苏宁）。
    """
    scanned = [f for f in files if f.get("is_scanned") and f.get("file_id")]
    # 文字版战略合同不用 OCR，但主合同转引它时也要定向读一遍条款
    strategic = [
        f
        for f in files
        if f.get("identified_type") == "战略合同"
        and ((f.get("is_scanned") and f.get("file_id")) or (not f.get("is_scanned") and f.get("text")))
    ]
    return [f for f in scanned if f.get("identified_type") == "合同"] + strategic


def _is_missing(fields: dict, key: str) -> bool:
    """字段是否缺失（value 为 None 或空串）。"""
    node = fields.get(key)
    val = node.get("value") if isinstance(node, dict) else node
    return val is None or (isinstance(val, str) and val.strip() == "")


def _mark_ocr_src(node: dict, filename: str) -> dict:
    """
    按需 OCR 补出的字段必然来自扫描件：确保 src 以《文件名》开头并带 OCR 标记。
    审核页状态与起诉状「⚠️ 待核实」标注都靠 src 里的 OCR 字样判定，
    不能依赖 LLM 是否按要求写出文件名（否则 mark_ocr_fields 匹配不到）。
    """
    node = dict(node)
    src = (node.get("src") or "").strip() or f"《{filename}》"
    if "OCR" not in src:
        src = f"{src}（OCR识别，请核实）"
    node["src"] = src
    return node


async def _extract_clauses(file: dict) -> dict:
    """据某扫描合同已 OCR 的逐页文本，定向抽取条款字段 + 以合同为准的字段（结构化 JSON）。"""
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


async def _ocr_batch(pdf: bytes, page_nums: list[int]) -> list[dict]:
    """并发 OCR 一批页；单页失败 / 超时跳过（实测密集表格页偶发超时），返回成功的页。"""
    results = await asyncio.gather(
        *(ocr_page(pdf, n) for n in page_nums), return_exceptions=True
    )
    pages: list[dict] = []
    for page_num, res in zip(page_nums, results):
        if isinstance(res, (RuntimeError, TimeoutError)):
            continue
        if isinstance(res, BaseException):
            raise res
        pages.append({"page": page_num, "text": res})
    # OCR 是主要成本项：记请求页数与成功页数，供按需 OCR 的成本复盘
    run_log.log_event("ocr_batch", requested=len(page_nums), ok=len(pages))
    return pages


def _needs_signature_page(fields: dict) -> bool:
    """签约日期还是审批表的（或缺失）、合同乙方还没读到 → 需要读到合同签章页。"""
    sign = fields.get("contract_sign_date")
    sign_from_approval = (
        not isinstance(sign, dict)
        or _is_missing(fields, "contract_sign_date")
        or "审批表" in str(sign.get("src") or "")
    )
    return sign_from_approval or _is_missing(fields, "contract_party_b")


def _mark_file_src(node: dict, filename: str) -> dict:
    """文字版文件定向抽取的字段：确保 src 带《文件名》（不加 OCR 标记）。"""
    node = dict(node)
    src = (node.get("src") or "").strip()
    node["src"] = src if filename in src else f"《{filename}》{src}"
    return node


def _ocr_targets(missing: set[str], need_signature: bool, need_warranty: bool) -> set[str]:
    """
    停止条件：付款、争议是起诉状必需的；违约条款常常没有（没有时律师用 LPR 常规话术），
    不因它继续翻页。只缺违约条款时才以它为目标。签约日期、合同乙方以签章页为准：还没从合同
    读到时，找到签章页才停；质保期同理。
    """
    targets = (missing - {"breach"}) or set(missing)
    if need_signature:
        targets = targets | {"signature"}
    if need_warranty:
        targets = targets | {"warranty"}
    return targets


async def _ocr_for_targets(
    pdf: bytes, f: dict, budget: int, targets: set[str]
) -> tuple[list[dict], int, bool]:
    """按批并发 OCR（每批 OCR_MAX_CONCURRENCY 页），找到目标条款正文即停；返回逐页文本、剩余预算、是否 OCR 过。"""
    total_pages = min(f.get("page_count") or pdf_page_count(pdf), budget)
    ocr_pages: list[dict] = []
    got = False
    for start in range(1, total_pages + 1, OCR_MAX_CONCURRENCY):
        batch = list(range(start, min(start + OCR_MAX_CONCURRENCY, total_pages + 1)))
        pages = await _ocr_batch(pdf, batch)
        budget -= len(batch)
        ocr_pages.extend(pages)
        if not pages:
            break  # 整批失败 → OCR 不可用，停止，优雅降级
        got = True
        if targets <= locate_clause_bodies(ocr_pages):
            break
    return ocr_pages, budget, got


def _mark_strategic_dispute_location(fields: dict, extracted: dict, filename: str) -> None:
    """
    争议条款从战略合同读到时，位置写「主合同第X条及战略合同第Y条」：起诉状「依据案涉合同…约定」
    同时引用主合同的转引条款与战略合同的约定，不会把战略合同的条款号当成本案合同的。
    """
    text = fields.get("dispute_clause_text")
    if not (isinstance(text, dict) and filename in str(text.get("src") or "")):
        return
    loc = extracted.get("dispute_clause_location")
    strategic_loc = str(loc.get("value") or "") if isinstance(loc, dict) else ""
    main = fields.get("dispute_clause_location")
    # 主合同位置须真取自主合同：缺失时合并循环可能已把战略合同的条款号填进来，不能当成主合同的
    main_loc = (
        str(main.get("value") or "")
        if isinstance(main, dict) and filename not in str(main.get("src") or "")
        else ""
    )
    part = f"{STRATEGIC_LABEL}{strategic_loc}" if strategic_loc else STRATEGIC_LABEL
    value = f"{main_loc}及{part}" if main_loc and not main_loc.startswith(STRATEGIC_LABEL) else part
    node = {"value": value, "src": f"《{filename}》"}
    # 条款读自扫描件时位置同样带 OCR 标记
    fields["dispute_clause_location"] = (
        _mark_ocr_src(node, filename) if "OCR" in str(text.get("src") or "") else node
    )


async def ocr_augment_node(state: GraphState) -> dict:
    """
    按需 OCR：仅当付款/违约/争议条款字段仍缺失（或签章页、质保金按日违约金所需的质保期
    还没读到）、且存在暂存了字节的扫描合同时，
    对该合同按批并发 OCR（找到付款 + 争议条款正文即停，跳过目录页；页数受
    AGENT_MAX_OCR_PAGES 约束），再据 OCR 文本定向补齐条款字段，并以合同为准覆盖合同名称、
    合同台数、安装地址，最后重新锚定页码。OCR 不可用则优雅降级、条款保持缺失。
    主合同读完仍缺条款时接着读战略 / 框架合同：只补缺失的条款（`STRATEGIC_CLAUSE_KEYS`），争议条款
    位置写成「主合同第X条及战略合同第Y条」；文字版战略合同不 OCR，直接定向抽取。
    """
    fields = state.get("extracted_fields", {})
    missing = {c for c, key in _CLAUSE_INDICATOR.items() if _is_missing(fields, key)}
    need_signature = _needs_signature_page(fields)
    # 欠款是质保金、违约金可能按日计时，起算日要用质保期推算：质保期还没读到也要 OCR 找
    need_warranty = needs_warranty_period(fields)
    if not missing and not need_signature and not need_warranty:
        return {}

    contracts = _clause_ocr_targets(state.get("files", []))
    if not contracts:
        return {}

    fields = copy.deepcopy(fields)
    files = copy.deepcopy(state.get("files", []))
    # 对深拷贝后的同名文件对象操作（保证返回的 files 带上 OCR 结果）
    contracts = _clause_ocr_targets(files)

    budget = AGENT_MAX_OCR_PAGES
    did_ocr = False

    for f in contracts:
        if not (missing or need_signature or need_warranty):
            break
        strategic = f.get("identified_type") == "战略合同"
        if strategic and not (missing or need_warranty):
            continue  # 只差签章页：战略合同的签章页不是本案的
        if not f.get("is_scanned"):
            # 文字版战略合同：已有逐页文本，直接定向抽取条款
            ocr_pages = list(f.get("pages") or []) or [{"page": 1, "text": f.get("text") or ""}]
            did_ocr = True
        else:
            if budget <= 0:
                continue
            pdf = file_store.get(f.get("file_id"))
            if pdf is None:
                continue  # 字节已淘汰/丢失 → 跳过（条款保持缺失）
            ocr_pages, budget, got = await _ocr_for_targets(
                pdf, f, budget, _ocr_targets(missing, need_signature and not strategic, need_warranty)
            )
            did_ocr = did_ocr or got

        if not ocr_pages:
            continue
        # 回填该合同的逐页文本，供 provenance 定位页码 + 后续展示
        f["pages"] = ocr_pages
        f["text"] = "\n\n".join(p["text"] for p in ocr_pages if p["text"])

        extracted = await _extract_clauses(f)
        filename = f.get("filename", "合同")
        mark = _mark_ocr_src if f.get("is_scanned") else _mark_file_src
        for key in _ALL_CLAUSE_KEYS:
            if strategic and key not in STRATEGIC_CLAUSE_KEYS:
                continue
            if _is_missing(fields, key):
                node = extracted.get(key)
                if isinstance(node, dict) and not _is_missing({key: node}, key):
                    fields[key] = mark(node, filename)
        for key in [] if strategic else _CONTRACT_AUTHORITATIVE_KEYS:
            node = extracted.get(key)
            if isinstance(node, dict) and not _is_missing({key: node}, key):
                fields[key] = mark(node, filename)
        if strategic:
            _mark_strategic_dispute_location(fields, extracted, filename)
        apply_rule_guards(fields)
        apply_strategic_guards(fields, files)
        missing = {c for c in missing if _is_missing(fields, _CLAUSE_INDICATOR[c])}
        need_signature = need_signature and _needs_signature_page(fields)
        need_warranty = needs_warranty_period(fields)

    if not did_ocr:
        return {}
    # 有新 OCR 文本：重新逐页锚定，令条款字段拿到真实页码 + channel=ocr
    enrich_provenance(fields, files)
    apply_strategic_guards(fields, files)  # 按锚定后的实际来源再把关
    return {"extracted_fields": fields, "files": files}


def _acceptance_from_approval(fields: dict) -> bool:
    """验收日期缺失，或只是从审批表「政府验收时间」兜底来的（不是报告批准日期）。"""
    node = fields.get("acceptance_latest_date")
    src = str(node.get("src") or "") if isinstance(node, dict) else ""
    return _is_missing(fields, "acceptance_latest_date") or "政府验收时间" in src


async def report_ocr_node(state: GraphState) -> dict:
    """
    扫描验收报告按需 OCR（新案卷核对：报告常是手机拍照，没有文本层）：还有没读过的扫描报告就
    逐页 OCR（上限 AGENT_MAX_REPORT_OCR_PAGES），再按**全部**验收报告汇总——文本报告已抽到的
    批准日期与 OCR 读到的取最晚，台数 = 文本报告计数 + 扫描报告首页计数；预算用尽或有页识别失败时
    在出处里注明可能不全（不把子集当全案结论）。
    """
    fields = state.get("extracted_fields", {})
    files = copy.deepcopy(state.get("files", []))
    reports = [f for f in files if f.get("identified_type") == "验收报告"]
    scanned = [
        f for f in reports if f.get("is_scanned") and f.get("file_id") and not f.get("text")
    ]
    if not scanned:
        return {}
    budget = AGENT_MAX_REPORT_OCR_PAGES
    incomplete = False
    ocr_reports: list[tuple[str, list[dict]]] = []
    ocr_files: list[dict] = []
    for f in scanned:
        pdf = file_store.get(f.get("file_id"))
        if pdf is None or budget <= 0:
            incomplete = True
            continue
        page_count = f.get("page_count") or pdf_page_count(pdf)
        total = min(page_count, budget)
        if total < page_count:
            incomplete = True  # 预算不够读完这份报告
        pages: list[dict] = []
        for start in range(1, total + 1, OCR_MAX_CONCURRENCY):
            batch = list(range(start, min(start + OCR_MAX_CONCURRENCY, total + 1)))
            got = await _ocr_batch(pdf, batch)
            budget -= len(batch)
            if len(got) < len(batch):
                incomplete = True  # 有页超时 / 失败
            if not got:
                break  # 整批失败 → OCR 不可用，优雅降级
            pages.extend(got)
        if not pages:
            continue
        f["pages"] = pages
        f["text"] = "\n\n".join(p["text"] for p in pages if p["text"])
        ocr_reports.append((f.get("filename", "验收报告"), pages))
        ocr_files.append(f)
    if not ocr_reports:
        return {}

    fields = copy.deepcopy(fields)
    partial = "；部分扫描报告未识别，可能不全，待核实" if incomplete else ""
    _merge_acceptance_date(fields, ocr_reports, partial)
    _merge_acceptance_qty(fields, reports, ocr_files, partial)
    if _is_missing(fields, "install_address"):
        for filename, pages in ocr_reports:
            found = first_install_address(pages)
            if found:
                value, page = found
                fields["install_address"] = {
                    "value": value,
                    "src": f"《{filename}》第{page}页安装地点（OCR识别，请核实）",
                    "page": page,
                    "channel": "ocr",
                }
                break
    return {"extracted_fields": fields, "files": files}


def _merge_acceptance_date(
    fields: dict, ocr_reports: list[tuple[str, list[dict]]], partial: str
) -> None:
    """
    OCR 读到的批准日期与文本报告已抽到的日期取最晚（审批表兜底的日期不参与比较）。
    扫描报告没读全、或有报告首页没读到批准日期时，最晚日期可能漏掉：已有日期也标待核实。
    """
    undated = sum(first_pages_without_date(pages) for _, pages in ocr_reports)
    if undated:
        partial += f"；{undated} 份扫描报告未识别出批准日期，可能不是最晚，待核实"
    best = None
    for filename, pages in ocr_reports:
        found = latest_approved_date(pages)
        if found and (best is None or _date_key(found[0]) > _date_key(best[1][0])):
            best = (filename, found)
    existing = None if _acceptance_from_approval(fields) else _str_value(
        fields, "acceptance_latest_date"
    )
    if best is None or (existing and _date_key(existing) >= _date_key(best[1][0])):
        if partial and not _is_missing(fields, "acceptance_latest_date"):
            node = dict(fields["acceptance_latest_date"])
            note = "已与扫描报告比对，取最晚" if best else "扫描报告未读到批准日期"
            node["src"] = f"{node.get('src') or ''}（{note}{partial}）"
            fields["acceptance_latest_date"] = node
        return
    filename, (value, page) = best
    fields["acceptance_latest_date"] = {
        "value": value,
        "src": f"《{filename}》第{page}页批准日期（多份取最晚；OCR识别，请核实{partial}）",
        "page": page,
        "channel": "ocr",
    }


def _merge_acceptance_qty(
    fields: dict,
    reports: list[dict],
    ocr_files: list[dict],
    partial: str,
) -> None:
    """
    验收台数 = 文本报告计数（设备代码 / 报告编号）+ 扫描报告计数。扫描报告先按首页（「设备品种」栏）
    数——手机拍照常把报告编号裁掉，按编号去重会少数；某份扫描报告数不出首页时，退回按设备代码 /
    报告编号计数；仍数不出就注明台数不全，不静默保留旧的子集数。
    """
    parts: list[str] = []
    total = 0
    uncounted = False
    text_reports = [f for f in reports if not f.get("is_scanned") and f.get("text")]
    if text_reports:
        counted = count_acceptance_units(text_reports)
        if counted is None:
            uncounted = True
        else:
            total += counted[0]
            parts.append(f"文本报告 {counted[0]} 台")
    for f in ocr_files:
        name = f"《{f.get('filename', '验收报告')}》"
        first_pages = count_report_first_pages(f.get("pages") or [])
        if first_pages:
            total += first_pages
            parts.append(f"{name}按报告首页计数 {first_pages} 份")
            continue
        counted = count_acceptance_units([f])
        if counted is None:
            uncounted = True
        else:
            total += counted[0]
            parts.append(f"{name}按设备代码 / 报告编号计数 {counted[0]} 台")
    if uncounted:
        partial += "；有报告数不出台数，可能不全，待核实"
    if not total:
        if not _is_missing(fields, "elevator_qty_by_acceptance"):
            node = dict(fields["elevator_qty_by_acceptance"])
            node["src"] = f"{node.get('src') or ''}（扫描报告未数出台数{partial}）"
            fields["elevator_qty_by_acceptance"] = node
        return
    fields["elevator_qty_by_acceptance"] = {
        "value": total,
        "src": f"{' + '.join(parts)}，共 {total} 台（OCR识别，请核实{partial}）",
        "channel": "ocr",
    }


def _date_key(value: str) -> tuple[int, int, int]:
    return parse_date(value) or (0, 0, 0)


def _str_value(fields: dict, key: str) -> str | None:
    node = fields.get(key)
    val = node.get("value") if isinstance(node, dict) else node
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _qty_mismatch(fields: dict) -> tuple[int, int] | None:
    """合同台数与验收报告台数不一致时返回 (合同, 报告)；律师已定最终台数则不再过问。"""
    node = fields.get("elevator_qty")
    if isinstance(node, dict) and str(node.get("src") or "").startswith("律师"):
        return None
    contract = parse_qty(_str_value(fields, "elevator_qty_by_contract"))
    acceptance = parse_qty(_str_value(fields, "elevator_qty_by_acceptance"))
    if contract is None or acceptance is None or contract == acceptance:
        return None
    return contract, acceptance


async def _ocr_rest_of_contracts(files: list[dict]) -> bool:
    """把扫描合同尚未 OCR 的页补读完（受 AGENT_MAX_OCR_PAGES 约束），就地更新 files。"""
    did = False
    budget = AGENT_MAX_OCR_PAGES
    for f in files:
        if not (f.get("is_scanned") and f.get("identified_type") == "合同"):
            continue
        pdf = file_store.get(f.get("file_id")) if f.get("file_id") else None
        if pdf is None:
            continue
        done = {p.get("page") for p in f.get("pages") or []}
        budget -= len(done)
        total = f.get("page_count") or pdf_page_count(pdf)
        todo = [n for n in range(1, total + 1) if n not in done][: max(budget, 0)]
        pages = list(f.get("pages") or [])
        for start in range(0, len(todo), OCR_MAX_CONCURRENCY):
            got = await _ocr_batch(pdf, todo[start : start + OCR_MAX_CONCURRENCY])
            if not got:
                break
            pages.extend(got)
            did = True
        budget -= len(todo)
        pages.sort(key=lambda p: p.get("page") or 0)
        f["pages"] = pages
        f["text"] = "\n\n".join(p["text"] for p in pages if p.get("text"))
    return did


async def contract_scan_node(state: GraphState) -> dict:
    """
    台数核对准备（补充确认单第 2 题）：合同台数与验收报告台数不一致时，把扫描合同剩余页
    （设备清单通常在最后）补读完，数出 VGE 型号（家用电梯，无需验收报告）的台数。
    与断点分成两个节点：断点恢复时节点会从头重跑，OCR 不能放在断点节点里。
    """
    fields = state.get("extracted_fields", {})
    if _qty_mismatch(fields) is None:
        return {}
    files = copy.deepcopy(state.get("files", []))
    did_ocr = await _ocr_rest_of_contracts(files)
    text = "\n".join(
        f.get("text") or "" for f in files if f.get("identified_type") == "合同"
    )
    vge, seen = count_vge_units(text)
    fields = copy.deepcopy(fields)
    if seen:
        fields["elevator_qty_vge"] = {
            "value": vge or None,
            "src": "合同设备清单中 VGE 型号（家用电梯）台数（代码计数）"
            if vge
            else "合同中出现 VGE 型号，但设备清单台数未能自动识别，待核实",
        }
    update: dict = {"extracted_fields": fields}
    if did_ocr:
        update["files"] = files
    return update


async def qty_check_node(state: GraphState) -> dict:
    """
    合同台数与验收报告台数不一致、且差额不是 VGE 家用电梯时，暂停提醒律师核对
    （报告是否齐全 / 有无补充协议 / 是否口头取消部分电梯），由律师确定验收台数。
    """
    fields = state.get("extracted_fields", {})
    mismatch = _qty_mismatch(fields)
    if mismatch is None:
        return {}
    contract, acceptance = mismatch
    vge_node = fields.get("elevator_qty_vge")
    vge = parse_qty(_str_value(fields, "elevator_qty_vge")) or 0
    if vge and acceptance == contract - vge:
        return {}  # 差额正好是家用电梯：按验收报告台数继续（最终台数已标待核实提醒）
    lines = [
        f"合同约定 {contract} 台，验收报告 {acceptance} 台，两者不一致。请核对：",
        "1、验收报告是否齐全（有无漏传）？",
        "2、是否存在补充协议更改了电梯台数？如有，请补充上传后重新分析。",
        "3、是否双方口头默认取消了部分电梯？",
    ]
    if isinstance(vge_node, dict):
        lines.append(
            f"另：合同中有 VGE 型号（家用电梯，无需验收报告）{vge} 台。"
            if vge
            else "另：合同中出现 VGE 型号（家用电梯，无需验收报告），台数请核对。"
        )
    lines.append("确认后，请选择起诉状「N 台电梯均于……验收合格」写几台。")
    decisions = interrupt(
        {
            "pending": {
                "kind": "confirm",
                "question": "\n".join(lines),
                "options": [str(acceptance), str(contract)],
                "field_keys": ["elevator_qty"],
            }
        }
    )
    answer = (decisions or {}).get("elevator_qty")
    if answer in (None, ""):
        return {}
    fields = copy.deepcopy(fields)
    fields["elevator_qty"] = {"value": answer, "src": "律师确认"}
    return {"extracted_fields": fields}


async def retention_node(state: GraphState) -> dict:
    """
    质保金确认（确认单第 9 题、补充确认单第 4 题）：合同约定了质保金时暂停，请律师按本次
    起诉是否包含质保金（或只含已到期的部分）选定起诉状「被告应按合同约定支付至 X% 合同款」。
    选项按质保金条款的分期比例给出（如 5% → 100%/95%；满一年 2%、满二年 3% → 100%/97%/95%）。
    """
    fields = state.get("extracted_fields", {})
    ratio = parse_percent(_str_value(fields, "retention_ratio"))
    node = fields.get("payable_ratio")
    lawyer_set = isinstance(node, dict) and str(node.get("src") or "").startswith(
        "律师"
    )
    # 「已付 + 欠款」已与付款条款的累计节点对上（payable_ratio_field）：比例已定，不再问律师
    # 正常图里 retention 在 validate（派生字段）之前，这里当场匹配，不能等派生结果（#54 评审）
    matched = payable_ratio_field(fields)
    from_stages = bool(matched) and str(matched.get("src") or "").startswith(PAYABLE_FROM_STAGES)
    # 只是银行质保保函、合同款里没有扣留质保金：不问律师，按支付至 100%（payable_ratio_field）
    if not ratio or lawyer_set or from_stages or retention_is_guarantee(fields):
        return {}
    clause = _str_value(fields, "retention_clause_text")
    unpaid = _str_value(fields, "retention_unpaid_amount")
    lines = [f"合同约定了质保金（{ratio:g}%）" + (f"：{clause}" if clause else "。")]
    lines.append(f"审批表「未付款项构成」中质保金为：{unpaid or '未识别'}。")
    lines.append(
        "审批表「未付款金额」即本次起诉的欠款金额。请按本次起诉是否包含质保金"
        "（或只含已到期的部分），选择起诉状写「支付至多少合同款」。"
    )
    decisions = interrupt(
        {
            "pending": {
                "kind": "confirm",
                "question": "\n".join(lines),
                "options": payable_ratio_options(fields),
                "field_keys": ["payable_ratio"],
            }
        }
    )
    answer = (decisions or {}).get("payable_ratio")
    if answer in (None, ""):
        return {}
    fields = copy.deepcopy(fields)
    fields["payable_ratio"] = {"value": answer, "src": "律师确认"}
    return {"extracted_fields": fields}


def after_checklist(state: GraphState) -> str:
    """清点后条件跳转：材料不足（pending）→ 终止；否则进入抽取。"""
    return "end" if state.get("pending") else "extract"
