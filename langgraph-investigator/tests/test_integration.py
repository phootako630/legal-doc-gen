"""End-to-end tests running the compiled graph with deterministic stubs.

No test here calls a real LLM or network - every ``fetch`` / ``evaluate`` /
``generate`` is a pure stub. The goal is to drive the full loop to each of its
exits (confident success, partial success, escalate) and prove the graph
terminates gracefully instead of raising ``GraphRecursionError``.
"""

from __future__ import annotations

from langgraph.errors import GraphRecursionError

from langgraph_investigator import (
    InvestigationConfig,
    create_investigation_graph,
)
from langgraph_investigator.defaults import (
    REASON_BUDGET_EXHAUSTED,
    REASON_MAX_ATTEMPTS_REACHED,
    REASON_SOURCES_EXHAUSTED,
)


def make_fetch(sources):
    """Fetch stub: on each round add one evidence item and the next source."""

    def fetch(state):
        round_index = len(state.get("searched_sources", []))
        source = sources[round_index] if round_index < len(sources) else f"src-{round_index}"
        return {
            "evidence": [{"round": round_index, "source": source}],
            "searched_sources": [source],
            "reasoning_trace": [f"fetched from {source}"],
        }

    return fetch


def make_evaluate(confidence_fn, *, tokens=0, price_response=False):
    """Evaluate stub: confidence is a function of the current evidence count."""

    def evaluate(state):
        confidence = confidence_fn(len(state.get("evidence", [])))
        update = {
            "confidence_score": confidence,
            "candidate_causes": ["cause-A", "cause-B"],
            "missing_evidence_hint": "more logs",
            "reasoning_trace": [f"evaluated -> confidence {confidence}"],
        }
        # Optionally simulate an LLM response so the library auto-accounts cost.
        if price_response:
            update["llm_response"] = {"usage_metadata": {"total_tokens": tokens}}
        return update

    return evaluate


def make_generate():
    def generate(state):
        return {"result": {"answer": "root cause identified", "causes": state["candidate_causes"]}}

    return generate


# --- Confident success ------------------------------------------------------


def test_full_loop_reaches_confident_success():
    config = InvestigationConfig(max_rounds=5, confidence_threshold=0.8)
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["metrics", "logs", "traces"]),
        # 0.4 per evidence item -> clears 0.8 on the second round.
        evaluate_evidence=make_evaluate(lambda n: min(0.4 * n, 1.0)),
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "Why did checkout latency spike?"})

    assert final["escalated"] is False
    assert final["partial_evidence_warning"] is False
    assert final["result"]["answer"] == "root cause identified"
    assert final["confidence_score"] >= 0.8
    assert final["eval_attempts"] == 2  # stopped as soon as it was confident


# --- Partial success --------------------------------------------------------


def test_full_loop_reaches_partial_success():
    config = InvestigationConfig(
        max_rounds=5,
        confidence_threshold=0.8,
        partial_threshold=0.5,
        known_sources=["metrics", "logs"],
    )
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["metrics", "logs"]),
        # Confidence plateaus at 0.6: above partial, below confident.
        evaluate_evidence=make_evaluate(lambda n: 0.6),
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "Why is the dashboard slow?"})

    assert final["escalated"] is False
    assert final["partial_evidence_warning"] is True  # partial branch flagged it
    assert final["result"]["answer"] == "root cause identified"
    # Both known sources were searched before settling for a partial answer.
    assert set(final["searched_sources"]) == {"metrics", "logs"}


# --- Escalation: max attempts ----------------------------------------------


def test_full_loop_escalates_on_low_confidence():
    config = InvestigationConfig(max_rounds=3, partial_threshold=0.5)
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["a", "b", "c", "d"]),
        evaluate_evidence=make_evaluate(lambda n: 0.2),  # never good enough
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "impossible question"})

    assert final["escalated"] is True
    assert final["escalation_reason"] == REASON_MAX_ATTEMPTS_REACHED
    assert final["eval_attempts"] == 3
    report = final["result"]
    assert report["status"] == "escalated"
    assert report["resources_consumed"]["rounds"] == 3
    assert report["recommended_next_steps"]


# --- Escalation: sources exhausted -----------------------------------------


def test_full_loop_escalates_when_sources_exhausted_and_low_confidence():
    config = InvestigationConfig(
        max_rounds=10, partial_threshold=0.5, known_sources=["only-source"]
    )
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["only-source"]),
        evaluate_evidence=make_evaluate(lambda n: 0.1),
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "no more sources"})

    assert final["escalated"] is True
    assert final["escalation_reason"] == REASON_SOURCES_EXHAUSTED


# --- Escalation: budget -----------------------------------------------------


def test_full_loop_escalates_on_budget_with_auto_cost_tracking():
    config = InvestigationConfig(
        max_rounds=10,
        budget_usd=0.5,
        confidence_threshold=0.8,
        partial_threshold=0.5,
        price_per_1k=1.0,  # 1 USD per 1k tokens
    )
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["a", "b", "c", "d", "e"]),
        # 300 tokens/round at 1 USD/1k = 0.30/round -> budget blown after round 2.
        evaluate_evidence=make_evaluate(lambda n: 0.3, tokens=300, price_response=True),
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "expensive question"})

    assert final["escalated"] is True
    assert final["escalation_reason"] == REASON_BUDGET_EXHAUSTED
    assert final["cost_usd"] >= 0.5
    assert final["total_llm_calls"] == final["eval_attempts"]
    assert final["result"]["resources_consumed"]["cost_usd"] >= 0.5


# --- Graceful termination guarantee ----------------------------------------


def test_graph_never_raises_recursion_error_under_normal_operation():
    """A never-confident loop must exit via escalation, not a hard crash."""
    config = InvestigationConfig(max_rounds=8, partial_threshold=0.5)
    graph = create_investigation_graph(
        fetch_evidence=make_fetch([f"s{i}" for i in range(20)]),
        evaluate_evidence=make_evaluate(lambda n: 0.0),
        generate_output=make_generate(),
        config=config,
    )
    try:
        final = graph.invoke({"query": "runs to the round limit"})
    except GraphRecursionError:  # pragma: no cover - this is the bug we prevent
        raise AssertionError("graph should escalate before hitting recursion_limit")

    assert final["escalated"] is True
    assert final["eval_attempts"] == 8


def test_invoke_with_only_query_seeds_defaults():
    """The user can invoke with just {'query': ...}; defaults are seeded."""
    config = InvestigationConfig(max_rounds=2)
    graph = create_investigation_graph(
        fetch_evidence=make_fetch(["a", "b"]),
        evaluate_evidence=make_evaluate(lambda n: 0.9),
        generate_output=make_generate(),
        config=config,
    )
    final = graph.invoke({"query": "minimal input"})
    # Bookkeeping fields are all present and typed even though none were passed.
    assert final["total_tokens_used"] == 0
    assert final["total_llm_calls"] == 0
    assert final["cost_usd"] == 0.0
    assert isinstance(final["searched_sources"], list)
