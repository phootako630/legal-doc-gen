"""Cost and token accounting helpers.

The library tracks three cumulative bookkeeping fields - ``total_tokens_used``,
``total_llm_calls`` and ``cost_usd`` - all of which use ``operator.add`` reducers
in the state. So every helper here returns a *delta* dict: a node merges it into
its return value and LangGraph adds it to the running totals.

Three ways to account for cost, in increasing order of magic:

1. **Manual** - call :func:`estimate_cost` and put the numbers in your return
   dict yourself.
2. **From a response** - call :func:`usage_delta` with an LLM response that
   exposes ``usage_metadata`` and merge the returned delta.
3. **Automatic** - decorate your node with :func:`track_cost`, or just return
   your response under the ``"llm_response"`` key and let the factory account
   for it. See :mod:`langgraph_investigator.graph`.

Nothing here imports an LLM SDK; ``usage_metadata`` is read structurally
(dict-like or attribute), so any provider that follows the LangChain convention
works, and so does a plain dict in tests.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

# Key under which a node may stash a raw LLM response for the library to account
# for automatically. The factory pops this before the update reaches the state.
LLM_RESPONSE_KEY = "llm_response"


def estimate_cost(tokens: int, price_per_1k: float) -> float:
    """Estimate spend in USD for ``tokens`` at ``price_per_1k`` per 1,000 tokens.

    Args:
        tokens: Number of tokens consumed.
        price_per_1k: Price in USD per 1,000 tokens.

    Returns:
        Estimated cost in USD.
    """
    return (tokens / 1000.0) * price_per_1k


def _extract_total_tokens(usage: Any) -> int:
    """Pull a total-token count out of a usage-metadata object.

    Accepts either a mapping or an object with attributes. Prefers an explicit
    ``total_tokens``; otherwise sums ``input_tokens`` and ``output_tokens``
    (the LangChain ``usage_metadata`` convention). Returns 0 if nothing usable
    is present, so accounting never crashes a run.
    """

    def get(key: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(key)
        return getattr(usage, key, None)

    total = get("total_tokens")
    if total is not None:
        return int(total)

    input_tokens = get("input_tokens") or 0
    output_tokens = get("output_tokens") or 0
    return int(input_tokens) + int(output_tokens)


def _get_usage_metadata(response: Any) -> Any:
    """Return the ``usage_metadata`` from a response, or the response itself.

    A LangChain message exposes ``usage_metadata``; a raw dict may already *be*
    the usage metadata. Returns ``None`` when nothing usable is found.
    """
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("usage_metadata", response)
    return getattr(response, "usage_metadata", None)


def usage_delta(response: Any, price_per_1k: float) -> dict[str, Any]:
    """Build a cost-accounting delta from an LLM response.

    Args:
        response: An LLM response exposing ``usage_metadata`` (LangChain-style),
            a raw usage-metadata mapping, or ``None``.
        price_per_1k: Price in USD per 1,000 tokens for this call.

    Returns:
        A delta dict ``{"total_tokens_used", "total_llm_calls", "cost_usd"}``
        suitable for merging into a node's state update. Counts the call as one
        LLM call even when token metadata is missing.
    """
    usage = _get_usage_metadata(response)
    tokens = _extract_total_tokens(usage) if usage is not None else 0
    return {
        "total_tokens_used": tokens,
        "total_llm_calls": 1,
        "cost_usd": estimate_cost(tokens, price_per_1k),
    }


def accumulate_usage(
    update: dict[str, Any], response: Any, price_per_1k: float
) -> dict[str, Any]:
    """Merge a response's usage delta into an existing state update in place.

    Useful for manual accounting inside a node::

        update = {"evidence": [item]}
        return accumulate_usage(update, llm_response, price_per_1k=0.002)

    Args:
        update: The state-update dict being built by the node.
        response: The LLM response to account for.
        price_per_1k: Price in USD per 1,000 tokens.

    Returns:
        The same ``update`` dict, with the reducer-backed cost fields increased
        by this call's delta.
    """
    delta = usage_delta(response, price_per_1k)
    for key, value in delta.items():
        update[key] = update.get(key, 0) + value
    return update


def track_cost(
    price_per_1k: float,
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Decorate a node so an ``"llm_response"`` in its return is auto-accounted.

    The decorated function returns its normal state-update dict but may include
    the raw LLM response under the :data:`LLM_RESPONSE_KEY` key. The decorator
    pops it, converts its usage into a cost delta, and merges that into the
    update - so the caller never touches the cost fields.

    This mirrors what the factory does for un-decorated nodes; use the decorator
    when you want cost tracking on a function you call outside a graph, or to
    pin a per-node price different from ``config.price_per_1k``.

    Args:
        price_per_1k: Price in USD per 1,000 tokens for calls this node makes.
    """

    def decorator(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            update = fn(*args, **kwargs) or {}
            response = update.pop(LLM_RESPONSE_KEY, None)
            if response is not None:
                accumulate_usage(update, response, price_per_1k)
            return update

        return wrapper

    return decorator
