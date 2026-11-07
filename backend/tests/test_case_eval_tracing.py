# eval 链路追踪（Langfuse）：用假客户端验证记什么、不记什么；不连接真实的 Langfuse。
import asyncio

import pytest

from scripts.case_eval import tracing
from scripts.case_eval.cache import CacheMiss, ReplayCache
from scripts.case_eval.tracing import CaseTracer, check_host


class FakeObs:
    def __init__(self, store, **kw):
        self.kw, self.updates, self.ended = kw, [], False
        self.id, self.trace_id = f"obs{len(store)}", "trace1"
        store.append(self)

    def update(self, **kw):
        self.updates.append(kw)

    def end(self):
        self.ended = True


class FakeClient:
    def __init__(self):
        self.obs, self.scores = [], []

    def start_observation(self, **kw):
        return FakeObs(self.obs, **kw)

    def create_score(self, **kw):
        self.scores.append(kw)


def _tracer():
    client = FakeClient()
    root = FakeObs([], name="root")
    return client, CaseTracer(client, root)


def test_cache_calls_recorded_with_cached_flag_under_case_trace(tmp_path):
    client, tracer = _tracer()
    cache = ReplayCache(str(tmp_path), mode="live")
    cache.tracer = tracer

    async def real_chat(messages, json_mode=False):
        return {"x": 1}

    chat = cache.wrap_chat(real_chat)
    asyncio.run(chat([{"role": "user", "content": "虚构"}], json_mode=True))
    replay = ReplayCache(str(tmp_path), mode="replay", strict=True)
    replay.tracer = tracer
    asyncio.run(replay.wrap_chat(real_chat)([{"role": "user", "content": "虚构"}], json_mode=True))
    first, second = client.obs
    assert first.kw["name"] == "LLM" and first.kw["as_type"] == "generation"
    assert first.kw["trace_context"] == {"trace_id": "trace1", "parent_span_id": tracer.root.id}
    assert first.updates[-1]["metadata"]["cached"] is False and second.updates[-1]["metadata"]["cached"] is True
    assert second.updates[-1]["output"] == {"x": 1} and first.ended and second.ended


def test_ocr_records_page_hash_not_image(tmp_path):
    client, tracer = _tracer()
    cache = ReplayCache(str(tmp_path), mode="live")
    cache.tracer = tracer
    img = "QUJD" * 100
    assert cache.wrap_ocr(lambda b64: "虚构文字")(img) == "虚构文字"
    obs = client.obs[0]
    assert obs.kw["name"] == "OCR" and img not in str(obs.kw) and "page_image_sha256" in obs.kw["input"]


def test_judge_recorded_as_evaluator_and_errors_marked(tmp_path):
    client, tracer = _tracer()
    cache = ReplayCache(str(tmp_path), mode="replay", strict=True)
    cache.tracer = tracer

    async def real(messages):
        return {"verdict": "一致", "reason": "x"}

    with pytest.raises(CacheMiss):
        asyncio.run(cache.wrap_judge(real, "judge-model")([{"role": "user", "content": "虚构"}]))
    obs = client.obs[0]
    assert obs.kw["as_type"] == "evaluator" and obs.kw["model"] == "judge-model"
    assert obs.updates[-1]["level"] == "ERROR" and obs.ended


def test_finish_writes_scores_and_summary():
    client, tracer = _tracer()
    result = {
        "materials": [{"filename": "虚构合同.pdf"}], "summary": {"正确": 3, "错误": 1, "缺失": 0, "待定": 1},
        "results": [
            {"key": "total_amount", "verdict": "正确", "expected": 100, "actual": "100", "match": "amount"},
            {"key": "handover_text", "verdict": "待定", "expected": "甲", "actual": "乙", "match": "judge",
             "judge_verdict": "一致", "judge_reason": "移交对象相同"},
        ],
    }
    tracer.finish(result)
    names = {s["name"]: s for s in client.scores}
    assert names["total_amount"]["value"] == "正确" and names["total_amount"]["data_type"] == "CATEGORICAL"
    assert names["裁判/handover_text"]["value"] == "一致" and names["裁判/handover_text"]["comment"] == "移交对象相同"
    assert names["准确率"]["value"] == 0.75 and names["准确率"]["data_type"] == "NUMERIC"
    assert tracer.root.updates[0]["input"]["materials"][0]["filename"] == "虚构合同.pdf"
    tracer.finish({"error": "ValueError: 坏了"})
    assert {"level": "ERROR", "status_message": "ValueError: 坏了"} in tracer.root.updates


def test_cloud_host_refused_unless_allowed():
    for host in ("https://cloud.langfuse.com", "https://us.cloud.langfuse.com"):
        with pytest.raises(SystemExit, match="Langfuse Cloud"):
            check_host(host, allow_cloud=False, root=tracing.FIXTURES_DIR)
        check_host(host, allow_cloud=True, root=tracing.FIXTURES_DIR)
    check_host("http://localhost:3000", allow_cloud=False, root="/srv/eval-cases")
    with pytest.raises(SystemExit, match="LANGFUSE_HOST"):
        check_host("", allow_cloud=False)


def test_allow_cloud_only_for_repo_fixtures(tmp_path):
    # 真实案卷目录加 --langfuse-allow-cloud 也不许上云（#156 评审）；指向虚构案卷集的软链接按真实路径放行
    host = "https://cloud.langfuse.com"
    for root in ("/srv/eval-cases", str(tmp_path), None, tracing.FIXTURES_DIR + "/../eval_fixtures_copy"):
        with pytest.raises(SystemExit, match="只用于仓库里的虚构案卷集"):
            check_host(host, allow_cloud=True, root=root)
    link = tmp_path / "fx"
    link.symlink_to(tracing.FIXTURES_DIR)
    check_host(host, allow_cloud=True, root=str(link))
    check_host(host, allow_cloud=True, root=tracing.FIXTURES_DIR + "/")
    sneaky = tmp_path / "real"
    sneaky.mkdir()
    (tmp_path / "eval_fixtures").symlink_to(sneaky)  # 名字叫 eval_fixtures 但不是仓库那个
    with pytest.raises(SystemExit):
        check_host(host, allow_cloud=True, root=str(tmp_path / "eval_fixtures"))


def test_make_tracing_needs_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit, match="LANGFUSE_PUBLIC_KEY"):
        tracing.make_tracing("run", [])


def test_default_tracer_is_noop(tmp_path):
    cache = ReplayCache(str(tmp_path), mode="live")
    assert isinstance(cache.tracer, tracing.NoopTracer)
    assert cache.wrap_ocr(lambda b64: "文字")("QUJD") == "文字"


def test_make_tracing_reports_auth_failure_in_one_line(monkeypatch):
    import langfuse

    class Rejecting:
        def __init__(self, **_):
            pass

        def auth_check(self):
            raise RuntimeError("401")

    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-wrong")
    monkeypatch.setattr(langfuse, "Langfuse", Rejecting)
    with pytest.raises(SystemExit, match="密钥不对：RuntimeError"):
        tracing.make_tracing("run", [])
