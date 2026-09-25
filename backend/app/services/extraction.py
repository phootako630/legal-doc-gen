# 抽取流程的框架无关纯 helper：拼装文本 / OCR 标记 / 校验结论渲染
#
# 供 v1 的 routers/extract.py 与 v2 的 agent/nodes.py 共用，避免重复实现。
from __future__ import annotations

import re
from typing import Any

from app.services.confidence import ConfidenceSignals, compute_confidence
from app.services.provenance import resolve_provenance
from app.services.validators import ValidationCheck


def build_combined_text(files: list[dict]) -> str:
    """拼装带文件名/类型/OCR标记的文本块，给模型结构化的出处依据。

    每个 file 需含 filename / identified_type / is_scanned / text 字段。
    """
    blocks: list[str] = []
    for f in files:
        name = f.get("filename", "未知文件")
        dtype = f.get("identified_type", "未知")
        body = f.get("text") or ""
        if f.get("is_scanned"):
            if body.strip():
                tag = "（本文件为 OCR 扫描识别，文字可能存在误差）"
            else:
                # 扫描件尚未 OCR（按需 OCR 延后到 agent 阶段）：给占位文案，
                # 让清点/抽取知道该文件存在但暂无正文，不要误判为空文件
                tag = "（扫描件，暂未 OCR，正文将在需要时按需识别）"
                body = "[扫描件正文暂未识别]"
        else:
            tag = ""
        blocks.append(f"【文件：{name}｜{dtype}】{tag}\n{body}")
    return "\n\n---\n\n".join(blocks)


def mark_ocr_fields(data: Any, scanned_filenames: set[str]) -> None:
    """
    递归遍历 extracted_fields，若某字段的 src 引用了扫描件文件名，
    代码层面确保 src 中带有 OCR 标记——不依赖模型是否记得主动声明，
    因为前端/渲染是靠 src 文本里的"OCR"/"扫描"关键词判定待核实状态的。
    """
    if not scanned_filenames:
        return
    if isinstance(data, dict):
        if "value" in data and "src" in data and isinstance(data.get("src"), str):
            src = data["src"]
            if src and "OCR" not in src and any(fn in src for fn in scanned_filenames):
                data["src"] = f"{src}（OCR识别，请核实）"
            return
        for v in data.values():
            mark_ocr_fields(v, scanned_filenames)
    elif isinstance(data, list):
        for item in data:
            mark_ocr_fields(item, scanned_filenames)


# 验收报告里的设备代码（每台电梯唯一）与报告编号（每台一份报告）
_EQUIPMENT_CODE_RE = re.compile(r"设备代码\s*[:：]?\s*([0-9A-Za-z]{10,})")
_REPORT_NO_RE = re.compile(r"报告编号\s*[:：]\s*([0-9A-Za-z\-]{6,})")


def count_acceptance_units(files: list[dict]) -> tuple[int, str] | None:
    """
    数验收报告覆盖的电梯台数（确定性代码，对应 CLAUDE.md「验收口径 = 报告份数/设备代码数」）。
    优先按不同设备代码计数，取不到再按不同报告编号计数；都取不到返回 None。
    返回 (台数, 出处说明)。
    """
    reports = [
        f for f in files if f.get("identified_type") == "验收报告" and f.get("text")
    ]
    if not reports:
        return None
    names = "、".join(f"《{f.get('filename', '验收报告')}》" for f in reports)
    text = "\n".join(f["text"] for f in reports)
    for pattern, label in (
        (_EQUIPMENT_CODE_RE, "设备代码"),
        (_REPORT_NO_RE, "报告编号"),
    ):
        found = set(pattern.findall(text))
        if found:
            return len(
                found
            ), f"{names}按{label}计数：共 {len(found)} 个不同{label}（代码计数）"
    return None


def apply_acceptance_count(fields: dict, files: list[dict]) -> None:
    """用代码计数覆盖 LLM 给的验收口径台数；数不出来则保留 LLM 的值。"""
    counted = count_acceptance_units(files)
    if counted is None:
        return
    qty, src = counted
    fields["elevator_qty_by_acceptance"] = {"value": qty, "src": src}


# 律师规则（确认单第 5 题）：被告住址、法定代表人一律用工商登记信息，审批表上的地址不用
_REGISTRY_ONLY_KEYS = ("defendant_address", "defendant_legal_rep")
_REGISTRY_ONLY_NOTE = "按律师规则：被告住址、法定代表人用工商登记信息（企查查、爱企查、启信宝等），请查询后填写"
_SUMMARY_MARK = "AI 归纳，待核实"


def apply_rule_guards(fields: dict) -> None:
    """
    抽取后按律师规则把关（确定性代码，不依赖模型是否照做）：
    - 被告住址 / 法定代表人若取自审批表 → 清空，提示律师查工商登记信息；
    - 付款条款的 AI 归纳句不是合同原文 → src 标「AI 归纳，待核实」，审核页与起诉状都会提示。
    """
    for key in _REGISTRY_ONLY_KEYS:
        node = fields.get(key)
        if (
            isinstance(node, dict)
            and node.get("value") not in (None, "")
            and "审批表" in str(node.get("src") or "")
        ):
            fields[key] = {"value": None, "src": _REGISTRY_ONLY_NOTE}
    node = fields.get("payment_clause_summary")
    if isinstance(node, dict) and node.get("value"):
        src = str(node.get("src") or "")
        if _SUMMARY_MARK not in src:
            node["src"] = f"{src}（{_SUMMARY_MARK}）" if src else f"（{_SUMMARY_MARK}）"


def enrich_provenance(data: Any, files: list[dict]) -> None:
    """
    递归遍历 extracted_fields，为每个 FieldValue 补充**已验证**的出处信息：
      page    —— 值回原文逐页锚定得到的真实页码（未命中为 None，不伪造）
      anchor  —— 命中片段（供前端高亮/反幻觉比对）
      channel —— 命中所在文件的来源通道（扫描件 ocr / 文本 text）
      confidence —— 由确定性软信号加权得出的 0-100 分（仅供 UI 排序/默认展开）

    值保守保留（不因未命中而硬删）：未命中时 confidence 偏低，
    交由 confidence.map_status 归入「待核实」，避免误删有效值。
    """
    if isinstance(data, dict):
        if "value" in data and "src" in data:
            prov = resolve_provenance(data.get("value"), data.get("src"), files)
            data["page"] = prov.page
            data["anchor"] = prov.matched_text
            data["channel"] = prov.channel
            data["confidence"] = compute_confidence(
                ConfidenceSignals(
                    channel=prov.channel, anchor_quality=prov.anchor_quality
                )
            )
            return
        for v in data.values():
            enrich_provenance(v, files)
    elif isinstance(data, list):
        for item in data:
            enrich_provenance(item, files)


def format_checks_for_llm(checks: list[ValidationCheck]) -> str:
    """把确定性校验结论渲染成给 LLM 的只读事实清单——LLM 据此措辞，不得重新判断。"""
    lines: list[str] = []
    for c in checks:
        if not c.applicable:
            verdict = "未校验（信息不足）"
        elif c.passed:
            verdict = "通过"
        else:
            verdict = "冲突/不通过"
        lines.append(f"- [{verdict}] {c.message}")
    return "\n".join(lines)
