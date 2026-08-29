"""Routing logic for the investigation loop.

This module is pure: it depends only on the state dict and the config, never on
LangGraph. That makes the priority order - the heart of the library - trivial to
unit test in isolation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import InvestigationConfig

if TYPE_CHECKING:
    from .state import InvestigationState

# Node names the router can select. Exposed as constants so the factory and the
# tests agree on the exact strings.
FETCH = "fetch_evidence"
GENERATE = "generate_output"
ESCALATE = "escalate"


def sources_exhausted(state: "InvestigationState", config: InvestigationConfig) -> bool:
    """Return True when every known source has already been searched.

    If ``config.known_sources`` is ``None`` the loop has no notion of a finite
    source set, so sources are never exhausted and termination is driven by
    ``max_rounds`` alone. Otherwise sources are exhausted once every entry in
    ``known_sources`` appears in ``state["searched_sources"]``.
    """
    known = config.known_sources
    if not known:
        return False
    searched = set(state.get("searched_sources", []))
    return all(source in searched for source in known)


def route_after_evaluation(
    state: "InvestigationState", config: InvestigationConfig
) -> str:
    """Decide what happens after an evaluate round. First match wins.

    Priority order (this ordering is the whole point of the library):

    1. **Budget exhausted** - ``cost_usd >= budget_usd`` -> ``escalate``.
       Highest priority: a hard constraint checked before anything else, so a
       run that blew its budget escalates even if it just became confident.
    2. **Confident** - ``confidence_score >= confidence_threshold`` ->
       ``generate_output``.
    3. **Keep searching** - sources not exhausted AND ``eval_attempts <
       max_rounds`` -> ``fetch_evidence``.
    4. **Good enough** - ``confidence_score >= partial_threshold`` ->
       ``generate_output`` as a partial result (the generate wrapper sets
       ``partial_evidence_warning``).
    5. **Give up** - otherwise -> ``escalate``.

    Note on the flag in rule 4: a conditional edge can only return the next node
    name, not mutate state, so the ``partial_evidence_warning`` flag is set by
    the generate node wrapper, which derives it deterministically (a result is
    partial whenever it is generated below ``confidence_threshold``).

    Returns:
        One of ``"fetch_evidence"``, ``"generate_output"``, ``"escalate"``.
    """
    cost = state.get("cost_usd", 0.0)
    confidence = state.get("confidence_score", 0.0)
    attempts = state.get("eval_attempts", 0)

    # 1. Budget is a hard ceiling - checked first, wins over confidence.
    if cost >= config.budget_usd:
        return ESCALATE

    # 2. Confident enough to answer.
    if confidence >= config.confidence_threshold:
        return GENERATE

    # 3. Room to keep searching: still have untried sources and rounds left.
    if not sources_exhausted(state, config) and attempts < config.max_rounds:
        return FETCH

    # 4. Can't search more, but confidence clears the partial bar - answer with
    #    a caveat rather than escalating.
    if confidence >= config.partial_threshold:
        return GENERATE

    # 5. Nothing left to try and not confident enough - hand off to a human.
    return ESCALATE
