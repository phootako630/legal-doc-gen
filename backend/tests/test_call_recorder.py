# 线上调用录制（#157）：录下的就是 eval 能直接回放的缓存；开关、备用模型、空 OCR、清理、失败不影响主流程。
import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import config
from app.services import call_keys, call_recorder, llm_client, ocr_engine
from scripts.case_eval import cache as eval_cache
from scripts.case_eval.cache import ReplayCache

MSGS = [{"role": "user", "content": "虚构合同：付款节点…"}]


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(llm_client, "_primary_down_until", 0.0)
    return tmp_path


def _client(content):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
    create = AsyncMock(return_value=SimpleNamespace(choices=[choice], usage=None))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_keys_shared_with_eval_cache():
    # 线上与 eval 用同一个函数算键：改一边另一边跟着变，录下的缓存才对得上
    assert eval_cache.llm_key is call_keys.llm_key and eval_cache.ocr_key is call_keys.ocr_key
    # 键的算法不变，否则已录制的虚构 / 真实案卷缓存全部失效（CI 的严格回放也会报未命中）
    expected = call_keys.digest({"model": config.DEEPSEEK_MODEL, "messages": MSGS, "json": True})
    assert call_keys.llm_key(MSGS, True) == expected


def test_recorded_llm_call_replays_in_eval(snap, monkeypatch):
    monkeypatch.setattr(llm_client, "_get_client", lambda: _client('{"total_amount": 100}'))
    result = asyncio.run(llm_client.chat(MSGS, json_mode=True))
    assert result == {"total_amount": 100}
    # 录制库就是回放缓存：严格回放不调用模型、读到同样的结果
    replay = ReplayCache(call_recorder.calls_dir(), mode="replay", strict=True)

    async def never(*_a, **_k):
        raise AssertionError("严格回放不该调用模型")

    assert asyncio.run(replay.wrap_chat(never)(MSGS, json_mode=True)) == {"total_amount": 100}
    path = os.path.join(call_recorder.calls_dir(), "llm", f"{call_keys.llm_key(MSGS, True)}.json")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_disabled_records_nothing(snap, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(llm_client, "_get_client", lambda: _client("文本"))
    asyncio.run(llm_client.chat(MSGS))
    call_recorder.record_ocr("QUJD", "文字")
    assert not os.path.exists(call_recorder.calls_dir())


def test_fallback_model_not_recorded(snap, monkeypatch):
    import openai
    import httpx

    req = httpx.Request("POST", "https://api.deepseek.com")
    failing = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(side_effect=openai.APITimeoutError(request=req)))))
    monkeypatch.setattr(llm_client, "_get_client", lambda: failing)
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "qwen-test")
    monkeypatch.setattr(llm_client, "_fallback_keys", lambda: ["k"])
    monkeypatch.setattr(llm_client, "_get_fallback_client", lambda key: _client("备用"))
    assert asyncio.run(llm_client.chat(MSGS)) == "备用"
    assert not os.path.exists(os.path.join(call_recorder.calls_dir(), "llm"))


def test_ocr_recorded_in_eval_format_and_empty_skipped(snap, monkeypatch):
    body = {"output": {"choices": [{"message": {"content": [{"text": " 虚构识别文字 "}]}}]}}
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: SimpleNamespace(json=lambda: body))
    assert ocr_engine._ocr_image_b64("QUJD") == "虚构识别文字"
    replay = ReplayCache(call_recorder.calls_dir(), mode="replay", strict=True)
    assert replay.wrap_ocr(lambda b64: pytest.fail("不该调用 OCR"))("QUJD") == "虚构识别文字"
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: SimpleNamespace(json=lambda: {}))
    assert ocr_engine._ocr_image_b64("REVG") == ""
    assert len(os.listdir(os.path.join(call_recorder.calls_dir(), "ocr"))) == 1


def test_record_failure_never_breaks_main_flow(snap, monkeypatch):
    blocker = snap / "calls"
    blocker.write_text("不是目录")  # 录制目录建不出来
    monkeypatch.setattr(llm_client, "_get_client", lambda: _client("正常"))
    assert asyncio.run(llm_client.chat(MSGS)) == "正常"
    call_recorder.record_ocr("QUJD", "文字")  # 不抛


def test_purge_removes_only_expired(snap):
    call_recorder.record_ocr("QUJD", "旧")
    call_recorder.record_ocr("REVG", "新")
    old = os.path.join(call_recorder.calls_dir(), "ocr", f"{call_keys.ocr_key('QUJD')}.txt")
    long_ago = time.time() - 181 * 86400
    os.utime(old, (long_ago, long_ago))
    assert call_recorder.purge_old() == 1
    assert os.listdir(os.path.join(call_recorder.calls_dir(), "ocr")) == [f"{call_keys.ocr_key('REVG')}.txt"]
