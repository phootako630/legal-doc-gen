# langgraph-investigator

English | [简体中文](#简体中文)

A reusable [LangGraph](https://github.com/langchain-ai/langgraph) abstraction for
**investigation loops**: agents that repeatedly gather evidence, evaluate whether
it is sufficient, and then either keep searching, produce a result, or gracefully
escalate to a human. It bakes in the failure-mode handling people usually forget —
loop termination, a cost/budget ceiling, and a proper fallback exit instead of
crashing on the recursion limit.

The core insight: LangGraph's `recursion_limit` only prevents infinite loops by
*crashing*. Real agents need graceful degradation — bounded rounds, a token/cost
budget, a confidence score instead of a yes/no, and an escalation path that hands
a partial-but-useful answer to a human. This library packages all of that behind a
single `create_investigation_graph(...)` factory, so you only supply your domain
logic.

## The flow

```text
                          ┌──────────────────────────────────────────┐
                          │                                          │
        START ──▶ fetch_evidence ──▶ evaluate_evidence ──▶ route_after_evaluation
                          ▲                                          │
                          │                                          ├─▶ generate_output ──▶ END
                          └──────────── fetch_evidence ◀─────────────┤   (confident or
                                        (keep searching)             │    good enough)
                                                                     │
                                                                     └─▶ escalate ────────▶ END
                                                                         (budget blown or
                                                                          gave up)
```

`fetch_evidence` and `evaluate_evidence` are your functions; the library wraps them
to own the round counter and cost tracking. After every evaluate round,
`route_after_evaluation` decides the next hop. `fetch_evidence` loops back in;
`generate_output` and `escalate` both terminate.

**Routing counts LangGraph super-steps.** Each investigation round is roughly two
super-steps (fetch + evaluate). The factory compiles the graph with a
`recursion_limit` derived from `max_rounds` (`max_rounds * 2 + 4`) so the library's
own graceful exits always fire *before* LangGraph would raise
`GraphRecursionError`. You can still override the limit per call with
`graph.invoke(inp, config={"recursion_limit": N})`.

### Routing priority (first match wins)

| # | Condition | Route | Why |
|---|-----------|-------|-----|
| 1 | `cost_usd >= budget_usd` | `escalate` | Budget is a hard constraint, checked **first** — even a confident run that blew its budget escalates. |
| 2 | `confidence_score >= confidence_threshold` | `generate_output` | Confident enough to answer. |
| 3 | sources not exhausted **and** `eval_attempts < max_rounds` | `fetch_evidence` | Room to keep searching. |
| 4 | `confidence_score >= partial_threshold` | `generate_output` | Good enough — answer with a `partial_evidence_warning`. |
| 5 | otherwise | `escalate` | Nothing left to try and not confident — hand off to a human. |

"Sources exhausted" is configurable: `InvestigationConfig(known_sources=[...])`
means exhausted once every known source appears in `state["searched_sources"]`. If
`known_sources` is not set, sources are never considered exhausted and termination
relies on `max_rounds` alone.

## Why budget is checked first

Budget is the only *hard* constraint in the list. Confidence, source coverage, and
round count all describe how the investigation is *going*; the budget describes
what you are *allowed to spend*. If a run reaches its budget ceiling on the same
round it finally becomes confident, you still want it to stop and escalate —
spending past the ceiling to emit a result violates the one guarantee a budget is
supposed to make. So rule 1 wins over everything, including a confidence score
that clears the threshold in rule 2.

## Install

```bash
pip install langgraph-investigator
# or, from a checkout:
pip install -e .
```

Requires Python 3.10+, `langgraph`, and `langchain-core`. Nothing in the library
imports a specific LLM SDK — it is provider-agnostic.

## Quickstart

```python
from langgraph_investigator import (
    create_investigation_graph,
    InvestigationConfig,
    InvestigationState,
)

def my_fetch_fn(state: InvestigationState) -> dict:
    # Gather more evidence. Append to `evidence` and `searched_sources`.
    # A real agent calls an observability API, a retriever, a web search, ...
    return {
        "evidence": [{"source": "metrics", "summary": "p99 latency 4x at 14:05"}],
        "searched_sources": ["metrics"],
    }

def my_eval_fn(state: InvestigationState) -> dict:
    # Judge sufficiency. Write confidence_score (and usually candidate_causes).
    # Return an LLM response under "llm_response" to auto-track token cost.
    return {"confidence_score": 0.85, "candidate_causes": ["bad deploy"]}

def my_output_fn(state: InvestigationState) -> dict:
    # Produce the final result once the loop is confident.
    return {"result": {"root_cause": state["candidate_causes"][0]}}

graph = create_investigation_graph(
    fetch_evidence=my_fetch_fn,
    evaluate_evidence=my_eval_fn,
    generate_output=my_output_fn,
    config=InvestigationConfig(
        max_rounds=5,
        budget_usd=0.50,
        confidence_threshold=0.8,   # >= this -> success
        partial_threshold=0.5,      # >= this (once stuck) -> success with a caveat
        known_sources=["metrics", "logs", "traces"],
        escalation_handler=None,    # optional; falls back to a sensible default
    ),
)

result = graph.invoke({"query": "Why did checkout latency spike?"})
print(result["result"])
print("escalated:", result["escalated"], "partial:", result["partial_evidence_warning"])
```

You can invoke with just `{"query": ...}` — the library seeds all bookkeeping
fields (`eval_attempts`, `cost_usd`, `searched_sources`, …) to sane defaults.

### Cost tracking

Three ways to account for spend, all optional:

```python
from langgraph_investigator import estimate_cost, accumulate_usage, track_cost

# 1. Manual: compute it yourself.
update = {"confidence_score": 0.4, "cost_usd": estimate_cost(1200, price_per_1k=0.002)}

# 2. From a response object exposing usage_metadata (LangChain-style).
def my_eval_fn(state):
    resp = llm.invoke(...)
    return accumulate_usage({"confidence_score": 0.6}, resp, price_per_1k=0.002)

# 3. Zero-ceremony: return the response under "llm_response" and the library
#    (or the @track_cost decorator) does the accounting for you.
def my_eval_fn(state):
    resp = llm.invoke(...)
    return {"confidence_score": 0.6, "llm_response": resp}
```

The cost fields (`total_tokens_used`, `total_llm_calls`, `cost_usd`) use
`operator.add` reducers, so each node just returns its delta and LangGraph keeps
the running total.

### Escalation

When the loop gives up (or blows its budget) it runs the escalation node. The
default `default_escalation_handler` is deterministic and provider-agnostic: it
builds a partial report dict — what is known, ranked candidate causes, the
missing-evidence hint, resources consumed, and recommended next steps — and sets
`escalated=True` with a classified `escalation_reason` (`budget_exhausted`,
`sources_exhausted`, `max_attempts_reached`, `no_new_evidence`, or
`low_confidence`). Pass your own `escalation_handler` in the config if you want an
LLM-written narrative instead.

## Examples

Three runnable examples, each stubbed so it runs without credentials, with a
comment marking where a real LLM/tool call would go:

- [`examples/rca_agent.py`](examples/rca_agent.py) — incident root-cause analysis;
  fetch widens the time window and switches data sources each round.
- [`examples/deep_research_agent.py`](examples/deep_research_agent.py) —
  multi-source research; each round queries a different source.
- [`examples/rag_iterative.py`](examples/rag_iterative.py) — iterative RAG; each
  round retrieves more chunks and reformulates the query.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT.

---

## 简体中文

[English](#langgraph-investigator) | 简体中文

一个可复用的 [LangGraph](https://github.com/langchain-ai/langgraph) 抽象，用于构建
**investigation loop（调查循环）**：agent 反复 gather evidence（收集证据）、评估证据
是否充分，然后要么继续搜索、要么产出结果、要么优雅地 escalate（上报）给人类。它内置
了人们通常会忘记处理的 failure mode：循环终止、cost/budget 上限，以及一个真正的
fallback 退出路径，而不是撞到 recursion limit 时直接崩溃。

核心洞察：LangGraph 的 `recursion_limit` 只能通过**崩溃**来阻止无限循环。真实 agent
需要的是优雅降级 —— 有界的轮次、token/cost budget、用 confidence score 取代非黑即白的
判断，以及一条能把「不完整但有用」的答案交给人类的 escalation 路径。本库把这些全部封装
在一个 `create_investigation_graph(...)` factory 背后，你只需提供自己的领域逻辑。

## 流程

流程图见上文英文部分的「The flow」示意图（fetch → evaluate → route →
{generate, loop, escalate}）。

`fetch_evidence` 和 `evaluate_evidence` 是你写的函数；本库对它们做 wrap，接管 round
计数和 cost 跟踪。每一轮 evaluate 之后，`route_after_evaluation` 决定下一跳：
`fetch_evidence` 回到循环，`generate_output` 和 `escalate` 都会终止。

**routing 是按 LangGraph super-step 计数的。** 每一轮调查大约是两个 super-step
（fetch + evaluate）。factory 在 compile 时会根据 `max_rounds` 推导一个
`recursion_limit`（`max_rounds * 2 + 4`），保证本库自己的优雅退出总是在 LangGraph
抛出 `GraphRecursionError` **之前**触发。你仍然可以在单次调用时覆盖它：
`graph.invoke(inp, config={"recursion_limit": N})`。

### 路由优先级（第一个命中者生效）

| # | 条件 | 路由到 | 原因 |
|---|------|--------|------|
| 1 | `cost_usd >= budget_usd` | `escalate` | budget 是硬性约束，**最先**检查 —— 即便本轮刚好变得 confident，只要超了预算也要 escalate。 |
| 2 | `confidence_score >= confidence_threshold` | `generate_output` | 置信度足够，可以给出答案。 |
| 3 | sources 未耗尽 **且** `eval_attempts < max_rounds` | `fetch_evidence` | 还有继续搜索的余地。 |
| 4 | `confidence_score >= partial_threshold` | `generate_output` | 勉强够用 —— 给出答案，但打上 `partial_evidence_warning` 标记。 |
| 5 | 其它情况 | `escalate` | 没什么可试的了，置信度又不够，交给人类。 |

「sources 耗尽」是可配置的：`InvestigationConfig(known_sources=[...])` 表示当每一个
known source 都出现在 `state["searched_sources"]` 中时即为耗尽。若不设置
`known_sources`，则 sources 永不视为耗尽，终止完全依赖 `max_rounds`。

## 为什么先检查 budget

在这份优先级列表里，budget 是唯一的**硬性**约束。confidence、source 覆盖度、轮次都在
描述这次调查「进行得怎么样」；而 budget 描述的是你「被允许花多少」。如果某一轮里 run
恰好在触及 budget 上限的同时才变得 confident，你依然希望它停下来并 escalate ——
为了产出结果而超支，恰恰违背了 budget 本该提供的那唯一一条保证。所以规则 1 压过其它
一切，包括规则 2 中已经越过阈值的 confidence score。

## 安装

```bash
pip install langgraph-investigator
# 或者从源码 checkout 安装：
pip install -e .
```

需要 Python 3.10+、`langgraph` 和 `langchain-core`。本库不 import 任何特定的 LLM
SDK —— 它是 provider-agnostic 的。

## 快速开始

用法与上文英文「Quickstart」中的代码完全一致（代码为共享，不再重复）：提供
`fetch_evidence` / `evaluate_evidence` / `generate_output` 三个函数，配好
`InvestigationConfig`，然后 `graph.invoke({"query": ...})`。

你可以只用 `{"query": ...}` 来 invoke —— 本库会把所有 bookkeeping 字段
（`eval_attempts`、`cost_usd`、`searched_sources` 等）初始化为合理的默认值。

### cost 跟踪

三种记账方式，都是可选的（对应上文英文的代码示例）：

1. **手动**：自己用 `estimate_cost(tokens, price_per_1k)` 算出来，放进返回的 dict。
2. **从 response**：调用 `accumulate_usage(update, resp, price_per_1k)`，从暴露了
   `usage_metadata` 的响应对象（LangChain 风格）里提取用量。
3. **零负担**：把响应对象放在 `"llm_response"` 键下返回，本库（或
   `@track_cost` 装饰器）自动完成记账。

cost 字段（`total_tokens_used`、`total_llm_calls`、`cost_usd`）都使用
`operator.add` reducer，所以每个 node 只需返回本轮的增量，LangGraph 会维护累计值。

### escalation

当循环放弃（或超预算）时，会执行 escalation node。默认的
`default_escalation_handler` 是确定性的、provider-agnostic 的：它构造一个 partial
report dict —— 已知信息、排序后的 candidate causes、缺失证据提示、已消耗的资源，以及
建议的下一步 —— 并把 `escalated` 置为 True，同时给出一个分类好的 `escalation_reason`
（`budget_exhausted`、`sources_exhausted`、`max_attempts_reached`、
`no_new_evidence` 或 `low_confidence`）。如果你想要 LLM 撰写的叙述性报告，在 config
里传入你自己的 `escalation_handler` 即可。

## 示例

三个可直接运行的示例，均已 stub 化，无需任何凭证即可运行，并在代码中标注了真实
LLM/工具调用应放在哪里：

- [`examples/rca_agent.py`](examples/rca_agent.py) —— 事故根因分析（RCA）；每一轮
  fetch 会拓宽时间窗口并切换数据源。
- [`examples/deep_research_agent.py`](examples/deep_research_agent.py) —— 多源
  深度研究；每一轮查询一个不同的 source。
- [`examples/rag_iterative.py`](examples/rag_iterative.py) —— 迭代式 RAG；每一轮
  检索更多 chunk 并重写 query。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

MIT。
