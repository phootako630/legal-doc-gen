"""langgraph-investigator: graceful investigation loops for LangGraph.

A reusable abstraction for agents that repeatedly gather evidence, evaluate
whether it is sufficient, and then either keep searching, produce a result, or
gracefully escalate to a human. It bakes in the failure-mode handling people
usually forget: bounded rounds, a cost/budget ceiling, a confidence score
instead of a yes/no, and a proper fallback exit instead of crashing on the
recursion limit.

Public API::

    from langgraph_investigator import (
        create_investigation_graph,
        InvestigationConfig,
        InvestigationState,
    )
"""

from __future__ import annotations

from .config import EscalationHandler, InvestigationConfig
from .cost_tracker import (
    accumulate_usage,
    estimate_cost,
    track_cost,
    usage_delta,
)
from .defaults import default_escalation_handler
from .graph import create_investigation_graph
from .routing import route_after_evaluation, sources_exhausted
from .state import InvestigationState

__all__ = [
    "create_investigation_graph",
    "InvestigationConfig",
    "InvestigationState",
    "EscalationHandler",
    "route_after_evaluation",
    "sources_exhausted",
    "default_escalation_handler",
    "estimate_cost",
    "usage_delta",
    "accumulate_usage",
    "track_cost",
]

__version__ = "0.1.0"
