"""The ``create_investigation_graph`` factory.

This is the only piece that touches LangGraph. It wires the user's three domain
functions into a graph, owns all the bookkeeping (round counter, cost merging,
partial-result flag, default seeding), and installs the escalation node and the
conditional routing edge.

Graph shape::

    START -> fetch_evidence -> evaluate_evidence -> route_after_evaluation
                    ^                                     |
                    |______________ fetch_evidence _______|
                                                          |-> generate_output -> END
                                                          |-> escalate -------> END
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .config import InvestigationConfig
from .cost_tracker import LLM_RESPONSE_KEY, accumulate_usage
from .defaults import default_escalation_handler
from .routing import ESCALATE, FETCH, GENERATE, route_after_evaluation
from .state import BOOKKEEPING_DEFAULTS, InvestigationState

# A user node function: takes the state, returns a partial state-update dict.
NodeFn = Callable[[InvestigationState], dict[str, Any]]

EVALUATE = "evaluate_evidence"


def _apply_cost_accounting(
    update: dict[str, Any], config: InvestigationConfig
) -> dict[str, Any]:
    """Auto-account for a raw LLM response returned under ``llm_response``.

    Lets a user opt into cost tracking with zero ceremony: return the response
    under the ``"llm_response"`` key and the library converts its usage into the
    reducer-backed cost deltas. Manual accounting still works - this is a no-op
    when the key is absent.
    """
    response = update.pop(LLM_RESPONSE_KEY, None)
    if response is not None:
        accumulate_usage(update, response, config.price_per_1k)
    return update


def _seed_defaults(state: InvestigationState, update: dict[str, Any]) -> None:
    """Fill in any bookkeeping default missing from both the state and update.

    Runs inside the fetch node (the graph's entry point) so a caller can invoke
    with just ``{"query": ...}``. Only seeds keys that are absent, so it never
    clobbers the user's fetch output or an overwrite field from a later round.
    """
    for key, value in BOOKKEEPING_DEFAULTS.items():
        if key not in state and key not in update:
            update[key] = value


def _make_fetch_node(fetch_fn: NodeFn, config: InvestigationConfig) -> NodeFn:
    """Wrap the user's fetch function: seed defaults, then account for cost."""

    def node(state: InvestigationState) -> dict[str, Any]:
        update = fetch_fn(state) or {}
        _apply_cost_accounting(update, config)
        _seed_defaults(state, update)
        return update

    return node


def _make_evaluate_node(evaluate_fn: NodeFn, config: InvestigationConfig) -> NodeFn:
    """Wrap the user's evaluate function: account for cost, bump the round count.

    ``eval_attempts`` is a plain overwrite counter, so the wrapper reads the
    current value and returns it plus one. The counter is library-owned; users
    should not write it.
    """

    def node(state: InvestigationState) -> dict[str, Any]:
        update = evaluate_fn(state) or {}
        _apply_cost_accounting(update, config)
        update["eval_attempts"] = state.get("eval_attempts", 0) + 1
        return update

    return node


def _make_generate_node(generate_fn: NodeFn, config: InvestigationConfig) -> NodeFn:
    """Wrap the user's generate function: account for cost, set the partial flag.

    A conditional edge cannot mutate state, so the ``partial_evidence_warning``
    flag from routing rule 4 is set here instead. The derivation is exact:
    ``generate_output`` is only reached from rule 2 (confident) or rule 4
    (partial), so a result is partial iff its confidence is below
    ``confidence_threshold``. The user can still override the flag explicitly.
    """

    def node(state: InvestigationState) -> dict[str, Any]:
        update = generate_fn(state) or {}
        _apply_cost_accounting(update, config)
        is_partial = state.get("confidence_score", 0.0) < config.confidence_threshold
        update.setdefault("partial_evidence_warning", is_partial)
        return update

    return node


def _make_escalate_node(config: InvestigationConfig) -> NodeFn:
    """Wrap the escalation handler (custom or the provider-agnostic default)."""
    handler = config.escalation_handler or default_escalation_handler

    def node(state: InvestigationState) -> dict[str, Any]:
        return handler(state, config) or {}

    return node


def create_investigation_graph(
    fetch_evidence: NodeFn,
    evaluate_evidence: NodeFn,
    generate_output: NodeFn,
    config: InvestigationConfig | None = None,
):
    """Build a compiled investigation graph from three domain functions.

    The user supplies only domain logic; the library owns routing, the round
    counter, cost tracking, default seeding, and escalation.

    Args:
        fetch_evidence: ``(state) -> dict`` update that gathers more evidence.
            Typically appends to ``evidence`` and ``searched_sources``.
        evaluate_evidence: ``(state) -> dict`` update that writes
            ``confidence_score`` and usually ``candidate_causes`` /
            ``missing_evidence_hint``.
        generate_output: ``(state) -> dict`` update that writes the final
            ``result`` when the loop is confident (or good enough).
        config: Loop limits, thresholds, cost model, and optional custom
            escalation handler. Defaults to ``InvestigationConfig()``.

    Returns:
        A compiled LangGraph runnable. Its default ``recursion_limit`` is derived
        from ``max_rounds`` so the graceful exits fire before a
        ``GraphRecursionError``; override it per call with
        ``graph.invoke(inp, config={"recursion_limit": N})``.
    """
    config = config or InvestigationConfig()

    builder = StateGraph(InvestigationState)
    builder.add_node(FETCH, _make_fetch_node(fetch_evidence, config))
    builder.add_node(EVALUATE, _make_evaluate_node(evaluate_evidence, config))
    builder.add_node(GENERATE, _make_generate_node(generate_output, config))
    builder.add_node(ESCALATE, _make_escalate_node(config))

    builder.add_edge(START, FETCH)
    builder.add_edge(FETCH, EVALUATE)
    # The one decision point: fetch again, answer, or escalate.
    builder.add_conditional_edges(
        EVALUATE,
        lambda state: route_after_evaluation(state, config),
        {FETCH: FETCH, GENERATE: GENERATE, ESCALATE: ESCALATE},
    )
    builder.add_edge(GENERATE, END)
    builder.add_edge(ESCALATE, END)

    compiled = builder.compile()
    # Apply the derived default recursion_limit while keeping it overridable at
    # invoke time (invoke-time config wins over this bound default).
    return compiled.with_config({"recursion_limit": config.derived_recursion_limit()})
