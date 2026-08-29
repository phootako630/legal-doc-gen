"""Iterative RAG (retrieval-augmented generation).

An investigation loop that treats retrieval as the evidence-gathering step. Each
round retrieves more chunks and reformulates the query based on what is still
missing, evaluate checks whether the retrieved context can answer the question,
and the loop either answers, retrieves again, or escalates when it cannot ground
an answer.

Fully stubbed - no vector store or LLM required. Comments show where the real
retriever and generator calls go.

Run it::

    python examples/rag_iterative.py
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

# A tiny fake knowledge base: chunk id -> text. A real agent embeds and indexes
# these in a vector store.
FAKE_CHUNKS: dict[str, str] = {
    "c1": "Refunds are issued to the original payment method.",
    "c2": "Refunds take 5-10 business days to appear.",
    "c3": "Partial refunds are allowed within 30 days of purchase.",
    "c4": "Refund status is visible under Order History > Details.",
}
# The order the (stubbed) retriever surfaces chunks across rounds.
RETRIEVAL_ORDER = [["c1"], ["c2", "c3"], ["c4"]]


def _reformulate(query: str, missing_hint: str) -> str:
    """Build the next-round query from the original plus what's still missing."""
    return query if not missing_hint else f"{query} ({missing_hint})"


def fetch_evidence(state: InvestigationState) -> dict:
    """Retrieve the next batch of chunks, reformulating the query first."""
    round_index = len(state.get("searched_sources", []))
    query = _reformulate(state["query"], state.get("missing_evidence_hint", ""))

    if round_index >= len(RETRIEVAL_ORDER):
        return {"reasoning_trace": ["retriever returned no new chunks"]}

    chunk_ids = RETRIEVAL_ORDER[round_index]

    # --- REAL AGENT: embed `query` and query your vector store, e.g.
    #     hits = vectorstore.similarity_search(query, k=4)
    chunks = [{"id": cid, "text": FAKE_CHUNKS[cid]} for cid in chunk_ids]

    return {
        "evidence": chunks,
        # Use the retrieval round as the "source" so exhaustion is well-defined.
        "searched_sources": [f"retrieval_round_{round_index}"],
        "reasoning_trace": [f"retrieved {chunk_ids} for query: {query!r}"],
    }


def evaluate_evidence(state: InvestigationState) -> dict:
    """Check whether the retrieved context can ground an answer.

    The stub becomes confident once it has chunks covering both "how" and "when"
    a refund is processed. A real implementation asks an LLM whether the context
    is sufficient and what is still missing.
    """
    texts = " ".join(item["text"] for item in state.get("evidence", []))

    # --- REAL AGENT: LLM judges sufficiency; return the response as
    #     "llm_response" so the library tracks its token cost automatically.
    has_method = "original payment method" in texts
    has_timing = "business days" in texts
    confidence = 0.4 * has_method + 0.4 * has_timing + 0.2 * ("Order History" in texts)

    if confidence >= 0.8:
        hint = ""
    elif not has_timing:
        hint = "how long refunds take"
    else:
        hint = "where to check refund status"

    return {
        "confidence_score": round(confidence, 2),
        "candidate_causes": ["Answer grounded in refund policy chunks"],
        "missing_evidence_hint": hint,
        "reasoning_trace": [f"context confidence {confidence:.2f}"],
    }


def generate_output(state: InvestigationState) -> dict:
    """Generate the grounded answer from the retrieved chunks."""
    citations = [item["id"] for item in state.get("evidence", [])]
    # --- REAL AGENT: an LLM writes the answer conditioned on state["evidence"].
    return {
        "result": {
            "question": state["query"],
            "answer": "Refunds go to your original payment method and take 5-10 "
            "business days; check status under Order History > Details.",
            "citations": citations,
            "confidence": state.get("confidence_score"),
        }
    }


def main() -> None:
    config = InvestigationConfig(
        max_rounds=4,
        budget_usd=0.25,
        confidence_threshold=0.8,
        partial_threshold=0.5,
        known_sources=[f"retrieval_round_{i}" for i in range(len(RETRIEVAL_ORDER))],
    )
    graph = create_investigation_graph(
        fetch_evidence=fetch_evidence,
        evaluate_evidence=evaluate_evidence,
        generate_output=generate_output,
        config=config,
    )

    final = graph.invoke({"query": "How do refunds work?"})

    print("=== iterative RAG result ===")
    print("escalated:", final["escalated"])
    print("partial:", final["partial_evidence_warning"])
    print("rounds:", final["eval_attempts"])
    print("result:", final["result"])


if __name__ == "__main__":
    main()
