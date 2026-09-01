# llm_client.chat 空返回处理单测：推理模型思维链耗尽 token → 正文空 → 判失败重试
#
# 用 AsyncMock 替换 _get_client()，不触网、不消耗 API 配额。
# 用 asyncio.run 驱动协程，避免为单测引入 pytest-asyncio 依赖。
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import llm_client


def _resp(
    content: str,
    finish_reason: str = "stop",
    completion_tokens: int = 10,
    total_tokens: int = 20,
    with_usage: bool = True,
):
    """构造一个仿 OpenAI ChatCompletion 的最小响应对象。"""
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )
    usage = (
        SimpleNamespace(completion_tokens=completion_tokens, total_tokens=total_tokens)
        if with_usage
        else None
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _patch_client(monkeypatch, create_mock: AsyncMock) -> None:
    """把 llm_client._get_client 换成返回带指定 create 的假客户端。"""
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)


def _run_chat(**kwargs):
    return asyncio.run(llm_client.chat([{"role": "user", "content": "x"}], **kwargs))


def test_empty_content_retries_then_succeeds(monkeypatch):
    # 第一次正文为空（length），第二次正常 → 应返回第二次内容，且调用两次
    create = AsyncMock(
        side_effect=[_resp("", finish_reason="length"), _resp("正文内容")]
    )
    _patch_client(monkeypatch, create)
    assert _run_chat() == "正文内容"
    assert create.await_count == 2


def test_empty_content_twice_raises_chinese_error(monkeypatch):
    # 两次都空 → 抛中文 RuntimeError（含"为空"），不静默当成功
    create = AsyncMock(
        side_effect=[
            _resp("", finish_reason="length"),
            _resp("   ", finish_reason="length"),
        ]
    )
    _patch_client(monkeypatch, create)
    with pytest.raises(RuntimeError) as excinfo:
        _run_chat()
    assert "为空" in str(excinfo.value)
    assert create.await_count == 2


def test_json_mode_empty_then_valid(monkeypatch):
    # json 模式下同样对空返回重试，成功后解析为 dict
    create = AsyncMock(side_effect=[_resp(""), _resp('{"a": 1}')])
    _patch_client(monkeypatch, create)
    assert _run_chat(json_mode=True) == {"a": 1}
    assert create.await_count == 2


def test_usage_none_does_not_crash_logging(monkeypatch):
    # response.usage 为 None 时，日志分支不应抛异常
    create = AsyncMock(side_effect=[_resp("hello", with_usage=False)])
    _patch_client(monkeypatch, create)
    assert _run_chat() == "hello"
    assert create.await_count == 1


def test_success_on_first_call_no_retry(monkeypatch):
    create = AsyncMock(side_effect=[_resp("一次成功")])
    _patch_client(monkeypatch, create)
    assert _run_chat() == "一次成功"
    assert create.await_count == 1
