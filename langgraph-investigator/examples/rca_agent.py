"""Incident root-cause analysis (the motivating example).

An investigation loop that hunts for the cause of a production incident. Each
fetch round widens the time window and switches to a different data source
(metrics -> logs -> traces -> deploys), evaluate scores how well the evidence
explains the incident, and the loop either answers, keeps digging, or escalates
a partial report to an on-call human.

Everything here is stubbed so it runs without any credentials. The comments mark
exactly where you would drop in a real observability API and a real LLM call.

Run it::

    python examples/rca_agent.py
"""

from __future__ import annotations

import os
import sys

# Make the package importable when running straight from a checkout (no install).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langgraph_investigator import (  # noqa: E402
    InvestigationConfig,
    InvestigationState,
    create_investigation_graph,
)

# Ordered data sources the loop will consult, one per round. Widening the time
# window each round is modelled by the round index.
DATA_SOURCES = ["metrics", "logs", "traces", "deploys"]

# A canned "database" of findings keyed by source. In a real agent this is what
# your observability queries would return.
FAKE_FINDINGS: dict[str, dict] = {
    "metrics": {"summary": "p99 checkout latency jumped 4x at 14:05 UTC"},
    "logs": {"summary": "connection pool exhausted warnings starting 14:04"},
    "traces": {"summary": "90% of slow spans wait on the payments DB"},
    "deploys": {"summary": "payments-svc v2.3.1 rolled out at 14:00"},
}


def fetch_evidence(state: InvestigationState) -> dict:
    """Query the next data source with a wider time window than last round."""
    round_index = len(state.get("searched_sources", []))
    if round_index >= len(DATA_SOURCES):
        # Nothing left to consult; return no new evidence.
        return {"reasoning_trace": ["no more data sources to query"]}

    source = DATA_SOURCES[round_index]
    window_minutes = 15 * (round_index + 1)  # widen the window each round

    # --- REAL AGENT: call your observability backend here, e.g.
    #     finding = prometheus.query(source, window_minutes=window_minutes)
    finding = FAKE_FINDINGS[source]

    return {
        "evidence": [{"source": source, "window_minutes": window_minutes, **finding}],
        "searched_sources": [source],
        "reasoning_trace": [f"queried {source} over last {window_minutes}m"],
    }


def evaluate_evidence(state: InvestigationState) -> dict:
    """Score how well the accumulated evidence explains the incident.

    The stub grows confidence as corroborating signals arrive and, once it sees
    both the deploy and the DB-bound traces, names a concrete cause. A real
    implementation would send the evidence to an LLM and parse a structured
    confidence + candidate-causes response.
    """
    evidence = state.get("evidence", [])
    seen = {item["source"] for item in evidence}

    # --- REAL AGENT: replace this heuristic with an LLM judgement, e.g.
    #     resp = llm.invoke(build_eval_prompt(state["query"], evidence))
    #     return {"confidence_score": ..., "candidate_causes": [...],
    #             "llm_response": resp}   # library auto-tracks token cost
    confidence = min(0.25 * len(seen), 1.0)
    causes: list[str] = []
    if {"deploys", "traces"} <= seen:
        causes.append("payments-svc v2.3.1 exhausts the payments DB connection pool")
    if "logs" in seen:
        causes.append("connection pool sizing too small for new traffic pattern")

    hint = "" if confidence >= 0.8 else "correlate the deploy timeline with DB metrics"
    return {
        "confidence_score": confidence,
        "candidate_causes": causes,
        "missing_evidence_hint": hint,
        "reasoning_trace": [f"confidence now {confidence:.2f} from {sorted(seen)}"],
    }


def generate_output(state: InvestigationState) -> dict:
    """Write the final root-cause summary once the loop is confident enough."""
    causes = state.get("candidate_causes", [])
    # --- REAL AGENT: have an LLM write the incident narrative from the evidence.
    return {
        "result": {
            "incident": state["query"],
            "root_cause": causes[0] if causes else "undetermined",
            "supporting_causes": causes[1:],
            "confidence": state.get("confidence_score"),
        }
    }


def main() -> None:
    config = InvestigationConfig(
        max_rounds=5,
        budget_usd=0.50,
        confidence_threshold=0.8,
        partial_threshold=0.5,
        known_sources=DATA_SOURCES,
        price_per_1k=0.002,  # used only if evaluate returns an llm_response
    )
    graph = create_investigation_graph(
        fetch_evidence=fetch_evidence,
        evaluate_evidence=evaluate_evidence,
        generate_output=generate_output,
        config=config,
    )

    final = graph.invoke({"query": "Why did checkout latency spike at 14:05 UTC?"})

    print("=== RCA agent result ===")
    print("escalated:", final["escalated"])
    print("partial:", final["partial_evidence_warning"])
    print("rounds:", final["eval_attempts"])
    print("result:", final["result"])


if __name__ == "__main__":
    main()
