# LangGraph 状态图：清点 →(材料足)→ 抽取 → 校验(可 interrupt) → 结束
#
# v1 无持久化，用 in-memory MemorySaver 承载 interrupt/resume 的 checkpoint；
# 未来接 DB 时替换 checkpointer 即可，节点/工具层零改动。
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    after_checklist,
    checklist_node,
    contract_scan_node,
    extract_node,
    ocr_augment_node,
    qty_check_node,
    retention_node,
    validate_node,
)
from app.agent.state import GraphState

_graph = None


def get_graph():
    """编译并缓存单例图（含 in-memory checkpointer）。"""
    global _graph
    if _graph is None:
        builder = StateGraph(GraphState)
        # 节点名不能与状态 key 同名（LangGraph 限制），故清点节点取名 intake
        builder.add_node("intake", checklist_node)
        builder.add_node("extract", extract_node)
        # 按需 OCR：缺条款且有扫描合同时，逐页 OCR 定向补齐（命中即停）
        builder.add_node("ocr_augment", ocr_augment_node)
        # 台数核对：合同与验收报告台数不一致时补读合同设备清单数 VGE 家用电梯，仍对不上则暂停问律师
        builder.add_node("contract_scan", contract_scan_node)
        builder.add_node("qty_check", qty_check_node)
        # 质保金确认：合同有质保金时暂停问律师起诉状写「支付至 X% 合同款」
        builder.add_node("retention", retention_node)
        builder.add_node("validate", validate_node)

        builder.set_entry_point("intake")
        builder.add_conditional_edges(
            "intake", after_checklist, {"extract": "extract", "end": END}
        )
        builder.add_edge("extract", "ocr_augment")
        builder.add_edge("ocr_augment", "contract_scan")
        builder.add_edge("contract_scan", "qty_check")
        builder.add_edge("qty_check", "retention")
        builder.add_edge("retention", "validate")
        builder.add_edge("validate", END)

        _graph = builder.compile(checkpointer=MemorySaver())
    return _graph
