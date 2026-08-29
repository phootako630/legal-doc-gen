"""Multi-source deep-research agent.

An investigation loop that answers an open research question by consulting a
different source each round (web search -> arxiv -> internal wiki -> expert
notes), accumulating findings until it has enough corroboration to write an
answer - or escalating a partial brief when the sources run dry.

Stubbed end to end so it runs with no credentials. Comments mark where real
retrieval tools and a real LLM would go.

Run it::

    python examples/deep_research_agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langgraph_investigator import (  # noqa: E402
    InvestigationConfig,
    InvestigationState,
    create_investigation_graph,
)

# One source per round, consulted in order.
SOURCES = ["web_search", "arxiv", "internal_wiki", "expert_notes"]

# Canned retrieval results. In a real agent each of these is a tool call.
FAKE_CORPUS: dict[str, list[str]] = {
    "web_search": ["Vector DBs trade recall for latency via ANN indexes."],
    "arxiv": ["HNSW graphs give logarithmic search at high recall (Malkov 2018)."],
    "internal_wiki": ["Our cluster uses HNSW with ef_search=64 in production."],
    "expert_notes": ["Raising ef_search improves recall but costs p99 latency."],
}


def fetch_evidence(state: InvestigationState) -> dict:
    """Retrieve from the next source in the list."""
    round_index = len(state.get("searched_sources", []))
    if round_index >= len(SOURCES):
        return {"reasoning_trace": ["all sources consulted"]}

    source = SOURCES[round_index]

    # --- REAL AGENT: call the matching retrieval tool, e.g.
    #     snippets = tavily.search(state["query"]) / arxiv.query(...) / ...
    snippets = FAKE_CORPUS[source]

    return {
        "evidence": [{"source": source, "snippet": s} for s in snippets],
        "searched_sources": [source],
        "reasoning_trace": [f"retrieved {len(snippets)} snippet(s) from {source}"],
    }


def evaluate_evidence(state: InvestigationState) -> dict:
    """Judge whether the evidence answers the question with enough support.

    The stub gains confidence as more independent sources agree. Swap in an LLM
    to assess coverage and contradictions for real.
    """
    evidence = state.get("evidence", [])
    distinct_sources = {item["source"] for item in evidence}

    # --- REAL AGENT: an LLM decides sufficiency and lists candidate answers;
    #     return its response under "llm_response" for automatic cost tracking.
    confidence = min(0.3 * len(distinct_sources), 1.0)
    causes = [
        "Tune HNSW ef_search to balance recall against p99 latency",
        "Recall/latency trade-off is inherent to ANN indexes",
    ]
    hint = "" if confidence >= 0.8 else "find a benchmark quantifying the trade-off"
    return {
        "confidence_score": confidence,
        "candidate_causes": causes,
        "missing_evidence_hint": hint,
        "reasoning_trace": [f"{len(distinct_sources)} sources agree -> {confidence:.2f}"],
    }


def generate_output(state: InvestigationState) -> dict:
    """Compose the research answer from the accumulated evidence."""
    # --- REAL AGENT: have an LLM synthesise a cited answer from state["evidence"].
    return {
        "result": {
            "question": state["query"],
            "answer": state.get("candidate_causes", ["undetermined"])[0],
            "sources_used": state.get("searched_sources", []),
            "confidence": state.get("confidence_score"),
        }
    }


def main() -> None:
    config = InvestigationConfig(
        max_rounds=6,
        budget_usd=1.00,
        confidence_threshold=0.8,
        partial_threshold=0.5,
        known_sources=SOURCES,
    )
    graph = create_investigation_graph(
        fetch_evidence=fetch_evidence,
        evaluate_evidence=evaluate_evidence,
        generate_output=generate_output,
        config=config,
    )

    final = graph.invoke(
        {"query": "How should we tune our vector DB for recall vs latency?"}
    )

    print("=== deep-research agent result ===")
    print("escalated:", final["escalated"])
    print("partial:", final["partial_evidence_warning"])
    print("rounds:", final["eval_attempts"])
    print("result:", final["result"])


if __name__ == "__main__":
    main()
