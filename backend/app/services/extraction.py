# 抽取流程的框架无关纯 helper：拼装文本 / OCR 标记 / 校验结论渲染
#
# 供 v1 的 routers/extract.py 与 v2 的 agent/nodes.py 共用，避免重复实现。
from __future__ import annotations

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
