# llm_client.chat 单测：空返回重试、JSON 外壳兜底、DeepSeek 不可用时改用备用模型
#
# 用 AsyncMock 替换 _get_client()，不触网、不消耗 API 配额。
# 用 asyncio.run 驱动协程，避免为单测引入 pytest-asyncio 依赖。
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
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


# ── JSON 模式外壳兜底（deepseek-v4-flash 实测会把答案包成 {"type":"json_object",...}）──
def test_json_mode_adds_system_prompt(monkeypatch):
    create = AsyncMock(return_value=_resp('{"a": 1}'))
    _patch_client(monkeypatch, create)
    assert _run_chat(json_mode=True) == {"a": 1}
    msgs = create.await_args.kwargs["messages"]
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "x"}


def test_text_mode_has_no_system_prompt(monkeypatch):
    create = AsyncMock(return_value=_resp("正文"))
    _patch_client(monkeypatch, create)
    _run_chat()
    assert create.await_args.kwargs["messages"] == [{"role": "user", "content": "x"}]


@pytest.mark.parametrize(
    "raw",
    [
        '{"type": "json_object", "content": {"a": 1}}',
        '{"type": "json_object", "content": "{\\"a\\": 1}"}',
        '{"type": "json_object", "a": 1}',
        # 实测：先吐一个空外壳，再给正文（两个对象首尾相接）
        '{"type": "json_object"}\n{"a": 1}',
        '{"type": "json_object"} {"type": "json_object", "content": {"a": 1}}',
    ],
)
def test_json_envelope_unwrapped(monkeypatch, raw):
    create = AsyncMock(return_value=_resp(raw))
    _patch_client(monkeypatch, create)
    assert _run_chat(json_mode=True) == {"a": 1}


def test_bare_envelope_retries(monkeypatch):
    # 只回外壳 → 视为失败重试，不把空结果当成功
    create = AsyncMock(
        side_effect=[_resp('{"type": "json_object"}'), _resp('{"a": 1}')]
    )
    _patch_client(monkeypatch, create)
    assert _run_chat(json_mode=True) == {"a": 1}
    assert create.await_count == 2


def test_concatenated_bare_envelopes_retry(monkeypatch):
    # 两个都是空外壳 → 仍算失败，重试
    create = AsyncMock(
        side_effect=[
            _resp('{"type": "json_object"}\n{"type": "json_object"}'),
            _resp('{"a": 1}'),
        ]
    )
    _patch_client(monkeypatch, create)
    assert _run_chat(json_mode=True) == {"a": 1}
    assert create.await_count == 2


# ── DeepSeek 不可用时改用备用模型（通义千问）──────────────────────────────────
_REQ = httpx.Request("POST", "https://api.deepseek.com/chat/completions")


def _status_error(cls, code: int):
    return cls("err", response=httpx.Response(code, request=_REQ), body=None)


@pytest.fixture(autouse=True)
def _reset_fallback_state(monkeypatch):
    # 冷却时间是进程内全局状态：每个测试从「DeepSeek 正常」开始
    monkeypatch.setattr(llm_client, "_primary_down_until", 0.0)
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "qwen-test")
    monkeypatch.setattr(llm_client, "_fallback_keys", lambda: ["key-a", "key-b"])


def _patch_fallback(monkeypatch, creates: dict) -> None:
    """备用模型按 key 返回各自的假客户端。"""
    monkeypatch.setattr(
        llm_client,
        "_get_fallback_client",
        lambda key: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=creates[key]))
        ),
    )


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=_REQ),
        openai.APIConnectionError(request=_REQ),
        _status_error(openai.InternalServerError, 503),
        _status_error(openai.RateLimitError, 429),
        _status_error(openai.APIStatusError, 402),  # 余额不足
    ],
)
def test_unavailable_falls_back_without_retrying_deepseek(monkeypatch, error):
    primary = AsyncMock(side_effect=error)
    backup = AsyncMock(return_value=_resp('{"a": 1}'))
    _patch_client(monkeypatch, primary)
    _patch_fallback(monkeypatch, {"key-a": backup})

    async def go():
        return await llm_client.chat([{"role": "user", "content": "x"}], json_mode=True), llm_client.last_served_model()

    result, served = asyncio.run(go())
    assert result == {"a": 1} and served == "qwen-test"
    assert primary.await_count == 1  # 不在 DeepSeek 上再等一轮
    kwargs = backup.await_args.kwargs
    assert kwargs["model"] == "qwen-test"
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert kwargs["response_format"] == {"type": "json_object"}


def test_cooldown_skips_deepseek(monkeypatch):
    primary = AsyncMock(side_effect=openai.APITimeoutError(request=_REQ))
    backup = AsyncMock(return_value=_resp("备用"))
    _patch_client(monkeypatch, primary)
    _patch_fallback(monkeypatch, {"key-a": backup})
    assert _run_chat() == "备用"
    assert _run_chat() == "备用"
    assert primary.await_count == 1 and backup.await_count == 2


def test_served_model_is_deepseek_when_healthy(monkeypatch):
    _patch_client(monkeypatch, AsyncMock(return_value=_resp("正常")))

    async def go():
        await llm_client.chat([{"role": "user", "content": "x"}])
        return llm_client.last_served_model()

    assert asyncio.run(go()) == llm_client.DEEPSEEK_MODEL


def test_invalid_deepseek_key_does_not_fall_back(monkeypatch):
    # key 无效是配置错误：提示改 .env，不悄悄换模型
    _patch_client(monkeypatch, AsyncMock(side_effect=_status_error(openai.AuthenticationError, 401)))
    backup = AsyncMock(return_value=_resp("备用"))
    _patch_fallback(monkeypatch, {"key-a": backup})
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        _run_chat()
    assert backup.await_count == 0


def test_fallback_disabled_keeps_old_error(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "")
    _patch_client(monkeypatch, AsyncMock(side_effect=openai.APITimeoutError(request=_REQ)))
    with pytest.raises(RuntimeError, match="超时"):
        _run_chat()


def test_fallback_switches_to_backup_dashscope_key(monkeypatch):
    _patch_client(monkeypatch, AsyncMock(side_effect=_status_error(openai.InternalServerError, 503)))
    denied = AsyncMock(side_effect=_status_error(openai.PermissionDeniedError, 403))
    ok = AsyncMock(return_value=_resp("备用 key"))
    _patch_fallback(monkeypatch, {"key-a": denied, "key-b": ok})
    assert _run_chat() == "备用 key"


def test_both_providers_down_raises_chinese_error(monkeypatch):
    _patch_client(monkeypatch, AsyncMock(side_effect=openai.APITimeoutError(request=_REQ)))
    _patch_fallback(
        monkeypatch, {"key-a": AsyncMock(side_effect=_status_error(openai.InternalServerError, 503))}
    )
    with pytest.raises(RuntimeError, match="备用模型 qwen-test 都调用失败"):
        _run_chat()


def test_sdk_auto_retry_disabled(monkeypatch):
    # SDK 自带重试会让 120 秒超时变成 6 分钟才报错、才切换备用模型
    monkeypatch.setattr(llm_client, "_client", None)
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "sk-test")
    assert llm_client._get_client().max_retries == 0
    monkeypatch.setattr(llm_client, "_fallback_clients", {})
    assert llm_client._get_fallback_client("key-a").max_retries == 0


def test_total_deadline_falls_back(monkeypatch):
    # DeepSeek 排队时持续回空行保活、读取超时不触发：总时限到了也要改用备用模型
    monkeypatch.setattr(llm_client, "LLM_TOTAL_TIMEOUT", 0.05)

    async def hang(**kwargs):
        await asyncio.sleep(10)

    _patch_client(monkeypatch, AsyncMock(side_effect=hang))
    _patch_fallback(monkeypatch, {"key-a": AsyncMock(return_value=_resp("备用"))})
    assert _run_chat() == "备用"


def test_total_deadline_without_fallback_is_timeout_error(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_TOTAL_TIMEOUT", 0.05)
    monkeypatch.setattr(llm_client, "LLM_FALLBACK_MODEL", "")

    async def hang(**kwargs):
        await asyncio.sleep(10)

    _patch_client(monkeypatch, AsyncMock(side_effect=hang))
    with pytest.raises(RuntimeError, match="超时"):
        _run_chat()
