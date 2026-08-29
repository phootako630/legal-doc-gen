"""Default, provider-agnostic escalation handling.

When the loop can no longer make progress it escalates to a human. The default
handler here builds a structured, deterministic *partial report* - never an LLM
call - so the library makes no assumption about which model or provider the user
runs. If a user wants an LLM-written narrative, they pass their own
``escalation_handler`` in :class:`InvestigationConfig`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import InvestigationConfig
from .routing import sources_exhausted

if TYPE_CHECKING:
    from .state import InvestigationState

# Recognised escalation reasons, in the priority order used by _determine_reason.
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_SOURCES_EXHAUSTED = "sources_exhausted"
REASON_MAX_ATTEMPTS_REACHED = "max_attempts_reached"
REASON_NO_NEW_EVIDENCE = "no_new_evidence"
REASON_LOW_CONFIDENCE = "low_confidence"


def _determine_reason(
    state: "InvestigationState", config: InvestigationConfig
) -> str:
    """Classify *why* the investigation is being escalated. First match wins.

    Priority:

    1. ``budget_exhausted`` - spend reached the budget ceiling.
    2. ``sources_exhausted`` - every known source was searched.
    3. ``max_attempts_reached`` - the round budget ran out.
    4. ``no_new_evidence`` - the loop stopped without gathering any evidence.
    5. ``low_confidence`` - evidence was gathered but confidence stayed below
       ``partial_threshold`` (the default catch-all).

    The structural reasons (1-3) come first because they explain what actually
    stopped the loop and are the most actionable; the evidence/confidence
    reasons (4-5) sub-classify a give-up that no structural limit caused.
    """
    if state.get("cost_usd", 0.0) >= config.budget_usd:
        return REASON_BUDGET_EXHAUSTED
    if sources_exhausted(state, config):
        return REASON_SOURCES_EXHAUSTED
    if state.get("eval_attempts", 0) >= config.max_rounds:
        return REASON_MAX_ATTEMPTS_REACHED
    if not state.get("evidence"):
        return REASON_NO_NEW_EVIDENCE
    return REASON_LOW_CONFIDENCE


def _recommend_next_steps(
    reason: str, state: "InvestigationState", config: InvestigationConfig
) -> list[str]:
    """Produce human-facing next steps tailored to the escalation reason."""
    steps: list[str] = []

    if reason == REASON_BUDGET_EXHAUSTED:
        steps.append(
            f"Raise budget_usd (currently {config.budget_usd}) or narrow the "
            "query, then re-run."
        )
    elif reason == REASON_SOURCES_EXHAUSTED:
        steps.append(
            "All known sources were searched. Add more sources to "
            "known_sources or investigate manually."
        )
    elif reason == REASON_MAX_ATTEMPTS_REACHED:
        steps.append(
            f"The loop hit max_rounds ({config.max_rounds}). Increase it or "
            "supply a sharper query if more rounds are likely to help."
        )
    elif reason == REASON_NO_NEW_EVIDENCE:
        steps.append(
            "No evidence was gathered. Check that the evidence source is "
            "reachable and that the query matches what it indexes."
        )
    else:  # low_confidence
        steps.append(
            "Evidence was gathered but confidence stayed low. A human should "
            "review the ranked candidate causes below."
        )

    hint = state.get("missing_evidence_hint")
    if hint:
        steps.append(f"Try to obtain: {hint}")

    causes = state.get("candidate_causes") or []
    if causes:
        steps.append(f"Start from the top candidate cause: {causes[0]}")

    return steps


def default_escalation_handler(
    state: "InvestigationState", config: InvestigationConfig
) -> dict[str, Any]:
    """Build a partial-but-useful report and mark the state escalated.

    The report is a plain dict (no LLM) capturing what is known, the ranked
    candidate causes, the missing-evidence hint, the resources consumed, and
    recommended next steps - everything a human needs to pick the investigation
    up where the agent left off.

    Returns:
        A state update setting ``result`` to the report, ``escalated`` to True,
        and ``escalation_reason`` to the classified reason.
    """
    reason = _determine_reason(state, config)
    evidence = state.get("evidence", [])

    report: dict[str, Any] = {
        "status": "escalated",
        "reason": reason,
        "query": state.get("query"),
        "what_is_known": {
            "evidence_count": len(evidence),
            "evidence": evidence,
            "reasoning_trace": state.get("reasoning_trace", []),
        },
        "ranked_candidate_causes": state.get("candidate_causes", []),
        "confidence_score": state.get("confidence_score", 0.0),
        "missing_evidence_hint": state.get("missing_evidence_hint", ""),
        "resources_consumed": {
            "llm_calls": state.get("total_llm_calls", 0),
            "tokens": state.get("total_tokens_used", 0),
            "cost_usd": round(state.get("cost_usd", 0.0), 6),
            "rounds": state.get("eval_attempts", 0),
        },
        "recommended_next_steps": _recommend_next_steps(reason, state, config),
    }

    return {
        "result": report,
        "escalated": True,
        "escalation_reason": reason,
    }
