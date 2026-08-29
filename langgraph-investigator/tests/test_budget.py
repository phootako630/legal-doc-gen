"""Tests for cost accumulation and budget-driven escalation."""

from __future__ import annotations

from langgraph_investigator import (
    InvestigationConfig,
    accumulate_usage,
    estimate_cost,
    track_cost,
    usage_delta,
)
from langgraph_investigator.defaults import (
    REASON_BUDGET_EXHAUSTED,
    _determine_reason,
    default_escalation_handler,
)


def test_estimate_cost_scales_with_tokens():
    assert estimate_cost(1000, 0.002) == 0.002
    assert estimate_cost(500, 0.002) == 0.001
    assert estimate_cost(0, 0.002) == 0.0


def test_usage_delta_from_dict_usage_metadata():
    response = {"usage_metadata": {"input_tokens": 100, "output_tokens": 50}}
    delta = usage_delta(response, price_per_1k=0.01)
    assert delta["total_tokens_used"] == 150
    assert delta["total_llm_calls"] == 1
    assert delta["cost_usd"] == estimate_cost(150, 0.01)


def test_usage_delta_prefers_explicit_total_tokens():
    response = {"usage_metadata": {"total_tokens": 200, "input_tokens": 999}}
    delta = usage_delta(response, price_per_1k=0.01)
    assert delta["total_tokens_used"] == 200


def test_usage_delta_from_attribute_object():
    class Usage:
        input_tokens = 10
        output_tokens = 20

    class Response:
        usage_metadata = Usage()

    delta = usage_delta(Response(), price_per_1k=0.005)
    assert delta["total_tokens_used"] == 30
    assert delta["total_llm_calls"] == 1


def test_usage_delta_counts_call_even_without_tokens():
    delta = usage_delta(None, price_per_1k=0.01)
    assert delta["total_tokens_used"] == 0
    assert delta["total_llm_calls"] == 1
    assert delta["cost_usd"] == 0.0


def test_accumulate_usage_merges_into_update():
    update = {"evidence": [{"x": 1}]}
    response = {"usage_metadata": {"total_tokens": 100}}
    result = accumulate_usage(update, response, price_per_1k=0.01)
    assert result is update  # merged in place
    assert result["total_tokens_used"] == 100
    assert result["total_llm_calls"] == 1
    assert result["evidence"] == [{"x": 1}]


def test_accumulate_usage_adds_onto_existing_deltas():
    update = {"total_tokens_used": 40, "total_llm_calls": 1, "cost_usd": 0.1}
    response = {"usage_metadata": {"total_tokens": 60}}
    accumulate_usage(update, response, price_per_1k=1.0)
    assert update["total_tokens_used"] == 100
    assert update["total_llm_calls"] == 2
    assert update["cost_usd"] == 0.1 + estimate_cost(60, 1.0)


def test_track_cost_decorator_auto_accounts():
    @track_cost(price_per_1k=0.01)
    def evaluate(state):
        return {
            "confidence_score": 0.4,
            "llm_response": {"usage_metadata": {"total_tokens": 300}},
        }

    update = evaluate({})
    assert "llm_response" not in update  # decorator popped it
    assert update["total_tokens_used"] == 300
    assert update["total_llm_calls"] == 1
    assert update["confidence_score"] == 0.4


def test_determine_reason_budget_exhausted():
    config = InvestigationConfig(budget_usd=0.5)
    state = {"cost_usd": 0.5, "confidence_score": 0.9}
    assert _determine_reason(state, config) == REASON_BUDGET_EXHAUSTED


def test_default_escalation_reports_budget_and_resources():
    config = InvestigationConfig(budget_usd=0.5)
    state = {
        "query": "why slow?",
        "cost_usd": 0.6,
        "total_tokens_used": 1200,
        "total_llm_calls": 3,
        "eval_attempts": 2,
        "evidence": [{"a": 1}],
        "candidate_causes": ["db lock"],
        "missing_evidence_hint": "need db metrics",
    }
    update = default_escalation_handler(state, config)
    assert update["escalated"] is True
    assert update["escalation_reason"] == REASON_BUDGET_EXHAUSTED
    report = update["result"]
    assert report["reason"] == REASON_BUDGET_EXHAUSTED
    assert report["resources_consumed"]["cost_usd"] == 0.6
    assert report["resources_consumed"]["tokens"] == 1200
    assert report["resources_consumed"]["llm_calls"] == 3
    assert report["resources_consumed"]["rounds"] == 2
    assert report["ranked_candidate_causes"] == ["db lock"]
    assert report["what_is_known"]["evidence_count"] == 1
    assert report["recommended_next_steps"]  # non-empty guidance
