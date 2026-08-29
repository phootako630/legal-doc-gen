"""Configuration for an investigation graph.

``InvestigationConfig`` holds every knob the library needs: the loop limits, the
routing thresholds, the cost model, and an optional custom escalation handler.
It is a plain dataclass so it is easy to construct, copy, and compare in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .state import InvestigationState

# An escalation handler takes the current state and the config and returns a
# state update dict (typically containing ``result``, ``escalated`` and
# ``escalation_reason``). Kept as a loose Callable alias to avoid importing the
# state module at runtime and to keep the library provider-agnostic.
EscalationHandler = Callable[["InvestigationState", "InvestigationConfig"], dict[str, Any]]


@dataclass
class InvestigationConfig:
    """Tunable parameters for the investigation loop.

    Attributes:
        max_rounds: Maximum number of evaluate rounds before the loop must stop
            searching. Guards against unbounded loops together with the derived
            ``recursion_limit``.
        budget_usd: Hard spend ceiling. Once ``cost_usd`` reaches this, the graph
            escalates on the very next routing decision - checked before anything
            else, including a high confidence score.
        confidence_threshold: ``confidence_score >= this`` routes straight to
            ``generate_output`` (a confident success).
        partial_threshold: Once the loop can no longer keep searching,
            ``confidence_score >= this`` still routes to ``generate_output`` but
            flags the result as partial. Below it, the loop escalates.
        known_sources: Optional list of every source the loop could search.
            "Sources exhausted" means all of these appear in
            ``state["searched_sources"]``. If ``None``, sources are never
            considered exhausted and termination relies on ``max_rounds`` alone.
        escalation_handler: Optional custom handler. Defaults to the built-in,
            provider-agnostic ``default_escalation_handler`` when ``None``.
        price_per_1k: Default price per 1,000 tokens used by cost estimation when
            a caller does not pass an explicit price.
        recursion_limit: Optional override for the compiled graph's default
            LangGraph ``recursion_limit``. When ``None`` the factory derives one
            from ``max_rounds`` so the graceful exits fire before the hard crash.
    """

    max_rounds: int = 5
    budget_usd: float = 0.50
    confidence_threshold: float = 0.8
    partial_threshold: float = 0.5
    known_sources: list[str] | None = None
    escalation_handler: EscalationHandler | None = None
    price_per_1k: float = 0.0
    recursion_limit: int | None = None

    def __post_init__(self) -> None:
        """Validate the thresholds early so misconfiguration fails loudly."""
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if self.budget_usd < 0:
            raise ValueError("budget_usd must be >= 0")
        if not 0.0 <= self.partial_threshold <= self.confidence_threshold <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 <= partial_threshold "
                "<= confidence_threshold <= 1"
            )

    def derived_recursion_limit(self) -> int:
        """Return the recursion_limit to compile with.

        Each investigation round is roughly two LangGraph super-steps
        (fetch + evaluate), plus a handful for the terminal node and routing
        overhead. Using ``max_rounds * 2 + 4`` keeps the limit comfortably above
        what a well-behaved loop needs, so the library's own graceful exits
        (budget, partial, escalate) fire before LangGraph raises
        ``GraphRecursionError``.
        """
        if self.recursion_limit is not None:
            return self.recursion_limit
        return self.max_rounds * 2 + 4
