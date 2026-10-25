# 成本、耗时与错误分类（#75）：节点归集、LLM 耗时与提示词版本、OCR 耗时分布、key 切换、
# 断点等待与放弃、错误类别、成本汇总。LLM / OCR 一律打桩，数据为虚构
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import requests

from app.agent import checkpoint
from app.agent import nodes as nodes_mod
from app.agent.runner import purge_expired_cases, run_analyze, run_resume
from app.services import error_category as ec
from app.services import llm_client, ocr_engine, run_log
from app.services.prompt_loader import load_prompt
from scripts import cost_report


def _use_log(monkeypatch, tmp_path):
    path = tmp_path / "run.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    return path


def _events(path, name=None):
    ev = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [e for e in ev if name is None or e["event"] == name]


# ── 错误类别 ──────────────────────────────────────────────────────────────────
def _http_429():
    resp = requests.Response()
    resp.status_code = 429
    return requests.HTTPError("429", response=resp)


def _wrapped(inner, outer):
    try:
        try:
            raise inner
        except BaseException as e:
            raise outer from e
    except BaseException as exc:
        return exc


@pytest.mark.parametrize(
    "exc, category",
    [
        (TimeoutError("第 3 页 OCR 超时（超过 120 秒）"), ec.OCR_TIMEOUT),
        (_wrapped(_http_429(), RuntimeError("第 3 页 OCR 处理失败：429")), ec.OCR_RATE_LIMIT),
        (RuntimeError("第 3 页 OCR 处理失败：连接被重置"), ec.OCR_FAILED),
        (_wrapped(TimeoutError("总时限已用完"), RuntimeError("AI 处理超时")), ec.LLM_TIMEOUT),
        (
            _wrapped(
                openai.RateLimitError(
                    "限流", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None
                ),
                RuntimeError("请求过于频繁"),
            ),
            ec.LLM_RATE_LIMIT,
        ),
        (ec.LLMEmptyResponse("AI 返回内容为空"), ec.LLM_EMPTY),
        (_wrapped(json.JSONDecodeError("x", "{", 0), RuntimeError("AI 返回格式异常")), ec.LLM_BAD_JSON),
        (ec.UnsupportedFileError("不支持的文件格式"), ec.PARSE_UNSUPPORTED),
        (KeyError("extracted_fields"), ec.INTERNAL),
    ],
)
def test_categorize(exc, category):
    assert ec.categorize(exc) == category


def test_node_error_records_category(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)

    async def broken(_state):
        raise ec.LLMEmptyResponse("AI 返回内容为空")

    with pytest.raises(ec.LLMEmptyResponse):
        asyncio.run(run_log.traced_node("extract", broken)({}))
    (node,) = _events(path, "node")
    assert node["outcome"] == "error" and node["error_category"] == ec.LLM_EMPTY
    assert "内容为空" not in path.read_text(encoding="utf-8")  # 只记类别，不记异常原文


# ── LLM：节点、耗时、提示词版本 ────────────────────────────────────────────────
def test_llm_call_has_node_duration_and_prompt(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="说明"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=resp))))
    monkeypatch.setattr(llm_client, "_get_client", lambda: client)

    async def node(_state):
        prompt = load_prompt("prompt-a-validate.md", {"extracted_json": "虚构机密取值"})
        await llm_client.chat([{"role": "user", "content": prompt}])
        return {}

    asyncio.run(run_log.traced_node("validate", node)({}))
    (call,) = _events(path, "llm_call")
    assert call["node"] == "validate" and isinstance(call["duration_ms"], int)
    assert call["prompt"].startswith("prompt-a-validate.md@") and len(call["prompt"].split("@")[1]) == 8
    assert "虚构机密取值" not in path.read_text(encoding="utf-8")


# ── OCR：耗时分布、超时 / 限流页数、key 切换 ─────────────────────────────────
def test_ocr_batch_records_timing_and_failures(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)

    async def fake_ocr(_pdf, n):
        if n == 2:
            raise TimeoutError("第 2 页 OCR 超时（超过 120 秒）")
        if n == 3:
            raise _wrapped(_http_429(), RuntimeError("第 3 页 OCR 处理失败：429"))
        return f"第{n}页"

    monkeypatch.setattr(nodes_mod, "ocr_page", fake_ocr)

    async def node(_state):
        await nodes_mod._ocr_batch(b"%PDF", [1, 2, 3, 4])
        return {}

    asyncio.run(run_log.traced_node("ocr_augment", node)({}))
    (batch,) = _events(path, "ocr_batch")
    assert batch["node"] == "ocr_augment"
    assert (batch["requested"], batch["ok"], batch["failed"]) == (4, 2, 2)
    assert batch["timeouts"] == 1 and batch["rate_limited"] == 1
    assert isinstance(batch["p50_ms"], int) and batch["max_ms"] >= batch["p50_ms"]


def test_dashscope_key_switch_event(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY", "虚构主key")
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY_BACKUP", "虚构备用key")
    monkeypatch.setattr(ocr_engine, "_use_backup", False)
    statuses = iter([403, 200])

    def fake_post(*_a, **_k):
        resp = requests.Response()
        resp.status_code = next(statuses)
        return resp

    monkeypatch.setattr(requests, "post", fake_post)
    ocr_engine._post_with_fallback({})
    (switch,) = _events(path, "dashscope_key_switch")
    assert switch["reason"] == 403
    assert "虚构主key" not in path.read_text(encoding="utf-8")


# ── 断点等待与放弃 ────────────────────────────────────────────────────────────
_CONFLICT = {
    "total_amount": {"value": 900000, "src": "《虚构审批表.pdf》"},
    "paid_amount": {"value": 638667.2, "src": "《虚构审批表.pdf》"},
    "unpaid_amount": {"value": 256266.8, "src": "《虚构审批表.pdf》"},
}


def test_resume_records_wait_and_purge_records_abandoned(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[dict(_CONFLICT), "说明\n---HIGHLIGHT---\n", dict(_CONFLICT)]))
    files = [{"filename": "虚构审批表.pdf", "text": "x"}]
    resumed = asyncio.run(run_analyze(files, True))
    asyncio.run(run_resume(resumed.run_id, {"total_amount": 894934}))
    (resume,) = _events(path, "resume")
    assert isinstance(resume["wait_s"], float) and resume["wait_s"] >= 0

    left = asyncio.run(run_analyze(files, True))  # 停在断点、没人作答
    assert left.pending is not None
    monkeypatch.setattr(checkpoint.config, "CASE_RETENTION_DAYS", -1)  # 全部算过期
    asyncio.run(purge_expired_cases())
    (abandoned,) = _events(path, "abandoned")  # 已作答的会话不算放弃
    assert abandoned["run_id"] == left.run_id and abandoned["pending_kind"] == "conflict"


# ── 汇总脚本 ──────────────────────────────────────────────────────────────────
def _line(event, run_id="r1", **kw):
    return json.dumps({"ts": "2026-10-25T08:00:00+00:00", "event": event, "run_id": run_id, **kw})


def test_cost_report_summary():
    lines = [
        _line("llm_call", node="extract", model="m1", prompt_tokens=1_000_000, completion_tokens=500_000),
        _line("llm_call", node="validate", model="m1", prompt_tokens=100_000, completion_tokens=0),
        _line("llm_call", run_id="r2", node="extract", model="没配价", prompt_tokens=10, completion_tokens=10),
        _line("ocr_batch", node="ocr_augment", requested=12, ok=10, timeouts=1, rate_limited=1),
        _line("node", node="extract", outcome="error", error_category="llm_bad_json", duration_ms=3000),
        _line("node", node="validate", outcome="interrupt", duration_ms=10),
        _line("node", run_id="r2", node="qty_check", outcome="interrupt", duration_ms=10),
        _line("resume", wait_s=120.0),
        _line("resume", run_id="r2", wait_s=600.0),
        _line("resume", run_id="r3", wait_s=60.0),
        _line("abandoned", run_id="r2", pending_kind="confirm"),
        _line("dashscope_key_switch", reason=401),
        "坏行",
    ]
    prices = {"m1": {"input": 2.0, "output": 8.0}, "ocr_page": 0.05}
    s = cost_report.summarize(lines, None, prices)
    assert s["runs"]["r1"]["cost"] == pytest.approx(2.0 + 4.0 + 0.2 + 0.5)
    assert s["runs"]["r2"]["unpriced"] and s["runs"]["r2"]["cost"] == 0
    top = max(s["nodes"].items(), key=lambda kv: kv[1]["cost"])
    assert top[0] == "extract"
    assert s["errors"] == {"llm_bad_json": 1, "ocr_timeout": 1, "ocr_rate_limit": 1}
    assert s["wait_median_s"] == 120.0
    assert s["abandoned"] == 1 and s["abandon_rate"] == pytest.approx(0.5)
    assert s["key_switches"] == 1 and s["ocr_pages"] == 10
    text = cost_report.render(s, "2026-10-25")
    assert "成本最高的节点：extract" in text and "放弃率 50%" in text and "2.0 分钟" in text
    assert "未配置单价" in text


# ── #101 评审回归 ─────────────────────────────────────────────────────────────
def test_ocr_batch_p50_is_true_median(monkeypatch, tmp_path):
    # 偶数页取两个中间值的平均，不是上中位值
    path = _use_log(monkeypatch, tmp_path)
    durations = {1: 1.0, 2: 2.0, 3: 100.0, 4: 100.0}

    async def fake_timed(_pdf, n):
        return f"第{n}页", durations[n]

    monkeypatch.setattr(nodes_mod, "_timed_ocr", fake_timed)
    asyncio.run(nodes_mod._ocr_batch(b"%PDF", [1, 2, 3, 4]))
    (batch,) = _events(path, "ocr_batch")
    assert batch["p50_ms"] == 51 and batch["max_ms"] == 100


def test_llm_fallback_key_switch_event(monkeypatch, tmp_path):
    # DeepSeek 不可用 → 备用模型主 key 被拒、改用付费备用 key：同样记 dashscope_key_switch
    path = _use_log(monkeypatch, tmp_path)
    req = httpx.Request("POST", "http://x")
    monkeypatch.setattr(llm_client, "_primary_down_until", 0.0)
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "qwen-test")
    monkeypatch.setattr(llm_client, "_fallback_keys", lambda: ["虚构主key", "虚构备用key"])
    ok = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="备用"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    creates = {
        "虚构主key": AsyncMock(side_effect=openai.PermissionDeniedError(
            "拒绝", response=httpx.Response(403, request=req), body=None)),
        "虚构备用key": AsyncMock(return_value=ok),
    }
    monkeypatch.setattr(llm_client, "_get_fallback_client", lambda key: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=creates[key]))))
    primary = AsyncMock(side_effect=openai.APITimeoutError(request=req))
    monkeypatch.setattr(llm_client, "_get_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary))))
    assert asyncio.run(llm_client.chat([{"role": "user", "content": "x"}])) == "备用"
    (switch,) = _events(path, "dashscope_key_switch")
    assert switch["reason"] == 403 and switch["path"] == "llm"
    assert "虚构主key" not in path.read_text(encoding="utf-8")


def test_purge_continues_when_abandoned_scan_fails(monkeypatch, tmp_path):
    # 某个过期会话读不出状态：放弃统计跳过它，保留期清理照常执行
    path = _use_log(monkeypatch, tmp_path)
    monkeypatch.setattr(checkpoint, "expired_run_ids", AsyncMock(return_value=["坏会话", "好会话"]))

    class Graph:
        async def aget_state(self, config):
            if config["configurable"]["thread_id"] == "坏会话":
                raise ValueError("checkpoint 损坏")
            return SimpleNamespace(tasks=[], values={})

    import app.agent.runner as runner_mod

    monkeypatch.setattr(runner_mod, "get_graph", AsyncMock(return_value=Graph()))
    purge = AsyncMock(return_value=2)
    monkeypatch.setattr(checkpoint, "purge_expired", purge)
    asyncio.run(purge_expired_cases())
    assert purge.await_count == 1
    (done,) = _events(path, "purge")
    assert done["runs"] == 2
    assert not _events(path, "purge_error")


def test_cost_report_model_cost_and_ocr_failed():
    lines = [
        _line("llm_call", node="extract", model="m1", prompt_tokens=1_000_000, completion_tokens=0),
        _line("llm_call", node="extract", model="没配价", prompt_tokens=10, completion_tokens=0),
        _line("ocr_batch", node="ocr_augment", requested=5, ok=1, failed=4, timeouts=1, rate_limited=1),
    ]
    s = cost_report.summarize(lines, None, {"m1": {"input": 2.0, "output": 8.0}})
    assert s["models"]["m1"]["cost"] == pytest.approx(2.0) and not s["models"]["m1"]["unpriced"]
    assert s["models"]["没配价"]["unpriced"]
    assert s["errors"] == {"ocr_timeout": 1, "ocr_rate_limit": 1, "ocr_failed": 2}
    text = cost_report.render(s, "")
    assert "m1\t1\t1000000\t0\t¥2.00" in text
