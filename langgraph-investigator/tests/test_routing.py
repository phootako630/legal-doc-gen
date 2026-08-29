"""Tests for the routing priority order - the heart of the library.

Every branch of ``route_after_evaluation`` is exercised, plus the tricky
boundary cases where naive implementations get it wrong.
"""

from __future__ import annotations

import pytest

from langgraph_investigator import InvestigationConfig
from langgraph_investigator.routing import (
    ESCALATE,
    FETCH,
    GENERATE,
    route_after_evaluation,
    sources_exhausted,
)


def make_config(**overrides) -> InvestigationConfig:
    """Config with predictable defaults for routing tests."""
    base = dict(
        max_rounds=5,
        budget_usd=1.0,
        confidence_threshold=0.8,
        partial_threshold=0.5,
    )
    base.update(overrides)
    return InvestigationConfig(**base)


def base_state(**overrides) -> dict:
    """A state that, with default config, would route to 'keep searching'."""
    state = {
        "cost_usd": 0.0,
        "confidence_score": 0.0,
        "eval_attempts": 1,
        "searched_sources": [],
    }
    state.update(overrides)
    return state


# --- Rule 1: budget exhausted -----------------------------------------------


def test_budget_exhausted_routes_to_escalate():
    config = make_config(budget_usd=0.5)
    state = base_state(cost_usd=0.5)
    assert route_after_evaluation(state, config) == ESCALATE


def test_budget_wins_over_confidence():
    """Budget is checked first: escalate even when confidence clears threshold."""
    config = make_config(budget_usd=0.5, confidence_threshold=0.8)
    state = base_state(cost_usd=0.6, confidence_score=0.95)
    assert route_after_evaluation(state, config) == ESCALATE


def test_budget_below_ceiling_does_not_escalate():
    config = make_config(budget_usd=0.5)
    state = base_state(cost_usd=0.49, confidence_score=0.9)
    assert route_after_evaluation(state, config) == GENERATE


# --- Rule 2: confident ------------------------------------------------------


def test_confidence_above_threshold_generates():
    config = make_config(confidence_threshold=0.8)
    state = base_state(confidence_score=0.9)
    assert route_after_evaluation(state, config) == GENERATE


def test_confidence_exactly_at_threshold_is_success():
    """0.8 >= 0.8 -> success. The boundary is inclusive."""
    config = make_config(confidence_threshold=0.8)
    state = base_state(confidence_score=0.8)
    assert route_after_evaluation(state, config) == GENERATE


# --- Rule 3: keep searching -------------------------------------------------


def test_keep_searching_when_rounds_remain_and_sources_available():
    config = make_config(max_rounds=5)
    state = base_state(confidence_score=0.3, eval_attempts=2)
    assert route_after_evaluation(state, config) == FETCH


def test_partial_confidence_keeps_searching_when_room_remains():
    """confidence == partial_threshold but rounds/sources remain -> keep going.

    The loop must NOT settle for a partial result while it can still improve.
    """
    config = make_config(confidence_threshold=0.8, partial_threshold=0.5)
    state = base_state(confidence_score=0.5, eval_attempts=1)
    assert route_after_evaluation(state, config) == FETCH


def test_sources_exhausted_stops_searching_even_with_rounds_left():
    """Sources exhausted but attempts < max_rounds -> does not keep searching."""
    config = make_config(max_rounds=5, known_sources=["a", "b"])
    # partial confidence so it settles for a partial result rather than escalate
    state = base_state(
        confidence_score=0.6, eval_attempts=1, searched_sources=["a", "b"]
    )
    assert route_after_evaluation(state, config) == GENERATE


# --- Rule 4: good enough (partial) ------------------------------------------


def test_partial_result_when_out_of_rounds():
    config = make_config(max_rounds=3, partial_threshold=0.5)
    state = base_state(confidence_score=0.6, eval_attempts=3)
    assert route_after_evaluation(state, config) == GENERATE


def test_partial_confidence_exactly_at_partial_threshold_when_stuck():
    """confidence == partial_threshold and no rounds left -> partial success."""
    config = make_config(max_rounds=3, partial_threshold=0.5)
    state = base_state(confidence_score=0.5, eval_attempts=3)
    assert route_after_evaluation(state, config) == GENERATE


# --- Rule 5: give up --------------------------------------------------------


def test_give_up_when_out_of_rounds_and_below_partial():
    config = make_config(max_rounds=3, partial_threshold=0.5)
    state = base_state(confidence_score=0.2, eval_attempts=3)
    assert route_after_evaluation(state, config) == ESCALATE


def test_give_up_when_sources_exhausted_and_below_partial():
    config = make_config(max_rounds=5, partial_threshold=0.5, known_sources=["a"])
    state = base_state(
        confidence_score=0.2, eval_attempts=1, searched_sources=["a"]
    )
    assert route_after_evaluation(state, config) == ESCALATE


# --- sources_exhausted helper ----------------------------------------------


def test_sources_never_exhausted_without_known_sources():
    config = make_config(known_sources=None)
    state = base_state(searched_sources=["a", "b", "c"])
    assert sources_exhausted(state, config) is False


def test_sources_exhausted_when_all_known_searched():
    config = make_config(known_sources=["a", "b"])
    assert sources_exhausted(base_state(searched_sources=["a", "b"]), config) is True
    assert sources_exhausted(base_state(searched_sources=["a"]), config) is False


def test_sources_exhausted_ignores_extra_searched_sources():
    config = make_config(known_sources=["a", "b"])
    state = base_state(searched_sources=["a", "b", "extra"])
    assert sources_exhausted(state, config) is True


# --- missing-field robustness ----------------------------------------------


def test_routing_tolerates_missing_fields():
    """An almost-empty state must not raise; unwritten fields default sanely."""
    config = make_config()
    assert route_after_evaluation({}, config) == FETCH


@pytest.mark.parametrize("bad", [dict(partial_threshold=0.9, confidence_threshold=0.8)])
def test_config_rejects_inverted_thresholds(bad):
    with pytest.raises(ValueError):
        make_config(**bad)
