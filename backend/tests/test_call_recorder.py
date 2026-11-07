# 线上调用录制（#157）：录下的能还原成 eval 回放缓存；同一请求不同返回各自保留、各案件取回自己那份；
# 开关、备用模型、空 OCR、目录权限、清理、失败不影响主流程。
import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import config
from app.services import call_keys, call_recorder, llm_client, ocr_engine, run_log
from scripts.case_eval import cache as eval_cache
from scripts.case_eval.cache import ReplayCache

MSGS = [{"role": "user", "content": "虚构合同：付款节点…"}]


@pytest.fixture
def snap(tmp_path, monkeypatch):
    root = tmp_path / "snapshots"
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(root))
    monkeypatch.setattr(llm_client, "_primary_down_until", 0.0)
    return root


def _client(content):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
    create = AsyncMock(return_value=SimpleNamespace(choices=[choice], usage=None))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _chat_in_run(monkeypatch, run_id, content, msgs=MSGS):
    monkeypatch.setattr(llm_client, "_get_client", lambda: _client(content))

    async def go():
        run_log.set_run_id(run_id)
        return await llm_client.chat(msgs, json_mode=True)

    return asyncio.run(go())


def _replay(dest):
    replay = ReplayCache(str(dest), mode="replay", strict=True)

    async def never(*_a, **_k):
        raise AssertionError("严格回放不该调用模型")

    return asyncio.run(replay.wrap_chat(never)(MSGS, json_mode=True))


def test_keys_shared_with_eval_cache():
    # 线上与 eval 用同一个函数算键：改一边另一边跟着变，录下的缓存才对得上
    assert eval_cache.llm_key is call_keys.llm_key and eval_cache.ocr_key is call_keys.ocr_key
    # 键的算法不变，否则已录制的虚构 / 真实案卷缓存全部失效（CI 的严格回放也会报未命中）
    expected = call_keys.digest({"model": config.DEEPSEEK_MODEL, "messages": MSGS, "json": True})
    assert call_keys.llm_key(MSGS, True) == expected


def test_recorded_run_replays_in_eval(snap, monkeypatch, tmp_path):
    assert _chat_in_run(monkeypatch, "run-a", '{"total_amount": 100}') == {"total_amount": 100}
    assert call_recorder.materialize("run-a", str(tmp_path / "cache")) == 1
    assert _replay(tmp_path / "cache") == {"total_amount": 100}


def test_same_request_different_outputs_kept_per_run(snap, monkeypatch, tmp_path):
    # 同一提示词两次返回不同（#158 评审）：后一次不覆盖前一次，各案件回放自己当时的结果
    _chat_in_run(monkeypatch, "run-a", '{"total_amount": 100}')
    _chat_in_run(monkeypatch, "run-b", '{"total_amount": 200}')
    key = call_keys.llm_key(MSGS, True)
    assert len(os.listdir(snap / "calls" / "llm" / key)) == 2
    call_recorder.materialize("run-a", str(tmp_path / "a"))
    call_recorder.materialize("run-b", str(tmp_path / "b"))
    assert _replay(tmp_path / "a") == {"total_amount": 100}
    assert _replay(tmp_path / "b") == {"total_amount": 200}
    assert call_recorder.resolve("run-a", "llm", key)[1] == "本次运行"


def test_unbound_ocr_resolved_by_time(snap, monkeypatch):
    # 上传时的 OCR 不属于任何运行：只有一份取那一份；有多份取运行开始前最近的一份，标「推断」
    run_log.set_run_id(None)
    call_recorder.record_ocr("QUJD", "第一次识别")
    key = call_keys.ocr_key("QUJD")
    assert call_recorder.resolve("run-x", "ocr", key) == ("第一次识别", "唯一")
    first = next((snap / "calls" / "ocr" / key).iterdir())
    os.utime(first, (time.time() - 100, time.time() - 100))
    call_recorder.record_ocr("QUJD", "第二次识别")  # 现在录的，晚于下面这次运行开始
    _chat_in_run(monkeypatch, "run-x", "{}")  # 运行清单从这一刻开始
    newest = max((snap / "calls" / "ocr" / key).iterdir(), key=os.path.getmtime)
    os.utime(newest, (time.time() + 100, time.time() + 100))
    assert call_recorder.resolve("run-x", "ocr", key) == ("第一次识别", "推断")
    assert call_recorder.resolve("run-x", "ocr", "不存在的键") == (None, "缺失")


def test_snapshot_dirs_are_owner_only(snap, monkeypatch):
    # 每一级目录都是 0700（#158 评审）；文件 0600
    _chat_in_run(monkeypatch, "run-a", "{}")
    for dirpath, _dirs, files in os.walk(snap):
        assert os.stat(dirpath).st_mode & 0o777 == 0o700, dirpath
        for name in files:
            assert os.stat(os.path.join(dirpath, name)).st_mode & 0o777 == 0o600, name


def test_disabled_records_nothing(snap, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", False)
    _chat_in_run(monkeypatch, "run-a", "{}")
    call_recorder.record_ocr("QUJD", "文字")
    assert not snap.exists()


def test_fallback_model_not_recorded(snap, monkeypatch):
    import httpx
    import openai

    req = httpx.Request("POST", "https://api.deepseek.com")
    failing = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(side_effect=openai.APITimeoutError(request=req)))))
    monkeypatch.setattr(llm_client, "_get_client", lambda: failing)
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "qwen-test")
    monkeypatch.setattr(llm_client, "_fallback_keys", lambda: ["k"])
    monkeypatch.setattr(llm_client, "_get_fallback_client", lambda key: _client("备用"))
    assert asyncio.run(llm_client.chat(MSGS)) == "备用"
    assert not (snap / "calls" / "llm").exists()


def test_ocr_recorded_and_empty_skipped(snap, monkeypatch, tmp_path):
    body = {"output": {"choices": [{"message": {"content": [{"text": " 虚构识别文字 "}]}}]}}
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: SimpleNamespace(json=lambda: body))

    async def page():
        run_log.set_run_id("run-o")
        return ocr_engine._ocr_image_b64("QUJD")

    assert asyncio.run(page()) == "虚构识别文字"
    call_recorder.materialize("run-o", str(tmp_path / "cache"))
    replay = ReplayCache(str(tmp_path / "cache"), mode="replay", strict=True)
    assert replay.wrap_ocr(lambda b64: pytest.fail("不该调用 OCR"))("QUJD") == "虚构识别文字"
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: SimpleNamespace(json=lambda: {}))
    assert ocr_engine._ocr_image_b64("REVG") == ""
    assert os.listdir(snap / "calls" / "ocr") == [call_keys.ocr_key("QUJD")]


def test_record_failure_never_breaks_main_flow(snap, monkeypatch):
    snap.mkdir(parents=True)
    (snap / "calls").write_text("不是目录")  # 录制目录建不出来
    assert _chat_in_run(monkeypatch, "run-a", '"正常"') == "正常"
    call_recorder.record_ocr("QUJD", "文字")  # 不抛


def test_purge_removes_only_expired(snap, monkeypatch):
    run_log.set_run_id(None)
    call_recorder.record_ocr("QUJD", "旧")
    call_recorder.record_ocr("REVG", "新")
    old_dir = snap / "calls" / "ocr" / call_keys.ocr_key("QUJD")
    long_ago = time.time() - 181 * 86400
    for f in old_dir.iterdir():
        os.utime(f, (long_ago, long_ago))
    assert call_recorder.purge_old() == 1
    assert not old_dir.exists()  # 空了的请求目录一并删掉
    assert os.listdir(snap / "calls" / "ocr") == [call_keys.ocr_key("REVG")]
