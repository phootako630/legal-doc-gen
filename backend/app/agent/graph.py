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
    extract_node,
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
        builder.add_node("validate", validate_node)

        builder.set_entry_point("intake")
        builder.add_conditional_edges(
            "intake", after_checklist, {"extract": "extract", "end": END}
        )
        builder.add_edge("extract", "validate")
        builder.add_edge("validate", END)

        _graph = builder.compile(checkpointer=MemorySaver())
    return _graph
