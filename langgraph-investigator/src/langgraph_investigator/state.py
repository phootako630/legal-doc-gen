"""State schema for the investigation graph.

The state is split into two groups of fields:

* **Library-owned bookkeeping** - written by the wrappers the factory installs
  around the user's nodes (counters, cost tracking, escalation flags). Users
  read these but should not write them directly.
* **Domain fields** - populated by the user's ``fetch_evidence`` /
  ``evaluate_evidence`` / ``generate_output`` functions.

Accumulating fields use ``Annotated[..., operator.add]`` reducers so a node can
return just the delta for that round and LangGraph merges it into the running
total. This is why ``fetch`` returns ``{"evidence": [new_item]}`` rather than
the full list, and why the evaluate wrapper can bump ``eval_attempts`` by
returning ``{"eval_attempts": 1}``.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class InvestigationState(TypedDict, total=False):
    """Shared state for an investigation loop.

    ``total=False`` means every key is optional at invoke time - the factory
    seeds sane defaults for the bookkeeping fields, so callers can start with
    just ``{"query": ...}``. Users are free to add their own domain keys to the
    dict they return from nodes; LangGraph will carry any extra keys through.
    """

    # --- Domain fields (populated by the user's node functions) ---
    query: str
    """The question or incident under investigation."""

    evidence: Annotated[list[dict], operator.add]
    """Accumulated evidence items. Fetch returns only the new items each round."""

    candidate_causes: list[str]
    """Ranked hypotheses, overwritten by the latest evaluate round."""

    missing_evidence_hint: str
    """What evidence would raise confidence, written by evaluate. Overwritten."""

    reasoning_trace: Annotated[list[str], operator.add]
    """Human-readable log of what happened each round. Appended across rounds."""

    result: Any
    """Final output, written by ``generate_output`` or the escalation handler."""

    # --- Library-owned bookkeeping (written by the factory's wrappers) ---
    eval_attempts: int
    """Number of completed evaluate rounds. Overwritten (+1) by the evaluate
    wrapper each round; this is a plain counter, not a reducer field."""

    searched_sources: Annotated[list[str], operator.add]
    """Source identifiers already tried. The user appends to this in fetch."""

    confidence_score: float
    """0.0-1.0 confidence written by evaluate. Overwritten each round."""

    total_tokens_used: Annotated[int, operator.add]
    """Cumulative LLM token usage across all rounds."""

    total_llm_calls: Annotated[int, operator.add]
    """Cumulative number of LLM calls across all rounds."""

    cost_usd: Annotated[float, operator.add]
    """Cumulative estimated spend in USD across all rounds."""

    escalated: bool
    """True once the escalation node has run."""

    escalation_reason: str
    """Machine-readable reason string, set by the escalation handler."""

    partial_evidence_warning: bool
    """True when the result was produced from partial (below-threshold) evidence."""


# Default values for the bookkeeping fields. The factory merges these into the
# input before invoking so reducer channels have a defined starting point and
# routing can read every field without KeyError.
BOOKKEEPING_DEFAULTS: dict[str, Any] = {
    "evidence": [],
    "candidate_causes": [],
    "missing_evidence_hint": "",
    "reasoning_trace": [],
    "eval_attempts": 0,
    "searched_sources": [],
    "confidence_score": 0.0,
    "total_tokens_used": 0,
    "total_llm_calls": 0,
    "cost_usd": 0.0,
    "escalated": False,
    "escalation_reason": "",
    "partial_evidence_warning": False,
}
