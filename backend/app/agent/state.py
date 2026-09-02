# CaseState：贯穿 analyze/resume 的 agent 会话状态（v2 agentic）
#
# 图内部用 TypedDict（LangGraph 对 dict 状态支持最好，节点返回部分更新）；
# 对外 API 用 Pydantic 的 CaseState/PendingDecision 做响应契约。
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel


class GraphState(TypedDict, total=False):
    """LangGraph 图状态（内部）。节点按需读写、返回部分字段更新。"""

    # 输入
    files: list[dict]              # 每项含 filename/text/is_scanned/identified_type
    internet_allowed: bool
    scanned_filenames: list[str]

    # 中间产物
    checklist: dict                # 材料清点结果
    extracted_fields: dict         # 抽取字段
    validations: list[dict]        # 确定性校验结论
    validation_report: str         # LLM 生成的说明文本
    highlight_list: str
    readiness: int                 # 起诉状就绪度 0–100
    pending: dict | None           # 命中的待决断点（None 表示无）


# ── 对外 API 契约（Pydantic）─────────────────────────────────────────────────
PendingKind = Literal["missing", "conflict", "confirm"]


class PendingDecision(BaseModel):
    """agent 命中的待决断点（interrupt）——抛给前端审核页让律师决定。"""

    kind: PendingKind
    question: str                  # 中文，问律师
    options: list[str] = []        # 冲突时的候选值（可空，律师自由编辑）
    field_keys: list[str] = []     # 涉及的字段 key


class CaseState(BaseModel):
    """agent 会话状态（贯穿 analyze/resume），是 /api/analyze、/api/resume 的响应体。"""

    run_id: str
    extracted_fields: dict[str, Any] = {}
    validations: list[dict[str, Any]] = []
    validation_report: str = ""
    highlight_list: str = ""
    readiness: int = 0
    pending: PendingDecision | None = None
