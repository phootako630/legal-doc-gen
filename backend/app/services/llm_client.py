# DeepSeek API 封装：基于 OpenAI SDK，统一 LLM 调用入口
# chat(messages, json_mode=False) → str
# chat(messages, json_mode=True)  → dict（自动去除 markdown 包裹后解析）
# DeepSeek 不可用（超时 / 连不上 / 5xx / 限流 / 欠费）时自动改用备用模型（通义千问，DashScope 兼容接口）
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextvars import ContextVar
from typing import Any

import openai
from openai import AsyncOpenAI

from app.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_API_KEY_BACKUP,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_FALLBACK_BASE_URL,
    LLM_FALLBACK_COOLDOWN_S,
    LLM_FALLBACK_ENABLE_THINKING,
    LLM_FALLBACK_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    LLM_TOTAL_TIMEOUT,
)
from app.services import run_log
from app.services.error_category import LLMEmptyResponse

logger = logging.getLogger(__name__)

# 单例客户端，避免重复创建连接
_client: AsyncOpenAI | None = None

# 单条消息类型
Message = dict[str, str]  # {"role": "user"|"assistant"|"system", "content": "..."}


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 backend/.env 中填入有效的 API Key")
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=LLM_TIMEOUT,
            # SDK 默认对超时 / 5xx 自动重试 2 次：单次 120 秒超时会拖成 6 分钟才报错。
            # 重试与改用备用模型由 _complete / chat 自己控制
            max_retries=0,
        )
    return _client


def _strip_markdown_json(raw: str) -> str:
    """去除 LLM 返回内容中可能包裹的 ```json ... ``` 标记。"""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


# JSON 模式下补充的系统指令。DeepSeek Flash（自 deepseek-v4-flash 起）在 response_format=json_object 时常把答案包成
# {"type": "json_object", "content": ...}（content 可能是对象或字符串，偶尔缺结尾括号），
# 甚至只回 {"type": "json_object"}。实测（真实案卷抽取，每组 4 次）：无此指令仅 1/4 可直接用，
# 加上后 4/4。
_JSON_SYSTEM_PROMPT = (
    "你只输出一个 JSON 对象本身：以 { 开头、以 } 结尾，顶层键就是要求的字段名。"
    "不要输出任何解释、不要用 markdown 代码块，也不要再包一层 type/content。"
)


def _unwrap_json_envelope(obj: Any) -> Any:
    """
    兜底剥掉模型自加的 {"type": "json_object", ...} 外壳（有系统指令后仍防御一层）。
    只剩外壳、没有实际内容时抛 JSONDecodeError，走重试路径，不把空结果当成功。
    """
    if not isinstance(obj, dict) or obj.get("type") != "json_object":
        return obj
    if "content" in obj:
        inner = obj["content"]
        if isinstance(inner, str):
            inner = json.loads(_strip_markdown_json(inner))
        return inner
    rest = {k: v for k, v in obj.items() if k != "type"}
    if not rest:
        raise json.JSONDecodeError("模型只返回了 JSON 外壳，没有内容", str(obj), 0)
    return rest


def _parse_json_content(cleaned: str) -> Any:
    """
    解析 JSON 模式的返回。模型偶尔先吐一个空外壳再给正文，即
    `{"type": "json_object"}\n{...真正的结果...}`（两个对象首尾相接，json.loads 报 Extra data）：
    逐个解码顶层对象，丢掉只剩外壳的，取第一个有内容的。都没有内容时抛 JSONDecodeError 走重试。
    """
    try:
        return _unwrap_json_envelope(json.loads(cleaned))
    except json.JSONDecodeError as e:
        if not e.msg.startswith("Extra data"):
            raise
    decoder = json.JSONDecoder()
    pos, values = 0, []
    while pos < len(cleaned):
        obj, end = decoder.raw_decode(cleaned, pos)
        values.append(obj)
        pos = end
        while pos < len(cleaned) and cleaned[pos].isspace():
            pos += 1
    for obj in values:
        try:
            return _unwrap_json_envelope(obj)
        except json.JSONDecodeError:
            continue  # 空外壳，看下一个
    raise json.JSONDecodeError("模型只返回了 JSON 外壳，没有内容", cleaned, 0)


def _translate_error(exc: Exception) -> RuntimeError:
    """将 OpenAI SDK 异常转换为带中文说明的 RuntimeError。"""
    if isinstance(exc, openai.AuthenticationError):
        return RuntimeError("API Key 无效，请检查 .env 中的 DEEPSEEK_API_KEY")
    if isinstance(exc, openai.RateLimitError):
        return RuntimeError("请求过于频繁，请稍后重试")
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)):
        return RuntimeError("AI 处理超时，请稍后重试或缩短输入长度")
    if isinstance(exc, openai.APIConnectionError):
        return RuntimeError("无法连接到 AI 服务，请检查网络连接")
    return RuntimeError(f"AI 调用失败：{exc}")


class _Unavailable(Exception):
    """服务方暂不可用：换一个服务商有意义（区别于 key 无效、返回格式异常等）。"""

    def __init__(self, exc: Exception) -> None:
        super().__init__(str(exc))
        self.exc = exc


# 402 余额不足、429 限流：换服务商能继续；其余 4xx（参数错、key 无效）换了也没用或属配置错误
_UNAVAILABLE_STATUS = {402, 429}


def _is_unavailable(exc: Exception) -> bool:
    if isinstance(exc, (openai.APIConnectionError, openai.InternalServerError)):
        return True  # APITimeoutError 是 APIConnectionError 的子类
    return isinstance(exc, openai.APIStatusError) and exc.status_code in _UNAVAILABLE_STATUS


# 这次调用实际由哪个模型返回。chat 在调用方的同一上下文里执行，返回后调用方可读到
# （eval 据此不把备用模型的结果录进 DeepSeek 的回放缓存）
_served_model: ContextVar[str | None] = ContextVar("llm_served_model", default=None)


def last_served_model() -> str | None:
    return _served_model.get()


async def _complete(
    client: AsyncOpenAI,
    model: str,
    messages: list[Message],
    json_mode: bool,
    extra_body: dict[str, Any] | None = None,
) -> Any:
    """
    向一个服务商发请求，空返回 / JSON 解析失败 / 偶发错误重试一次。
    服务不可用抛 _Unavailable（由 chat 决定是否换备用模型）；key 无效原样抛出。
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if extra_body:
        kwargs["extra_body"] = extra_body

    last_exc: Exception | None = None
    last_content: str = ""  # 保留最近一次原始返回，用于 JSON 解析失败时的调试信息
    # 总时限覆盖对同一服务商的全部尝试：空返回 / 坏 JSON 后的重试只用剩余时间，不重新计 180 秒
    deadline = time.monotonic() + LLM_TOTAL_TIMEOUT

    for attempt in range(2):  # 最多尝试 2 次
        t0 = time.monotonic()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("总时限已用完")
            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs), timeout=remaining
            )
            choice = response.choices[0]
            content: str = choice.message.content or ""
            last_content = content

            # 每次调用记录 finish_reason 和 token 用量，空返回等问题才有排查依据
            usage = response.usage
            logger.info(
                "model=%s finish_reason=%s content_len=%d%s",
                model,
                choice.finish_reason,
                len(content),
                f" tokens={usage.completion_tokens}/{usage.total_tokens}" if usage else "",
            )
            # node 由 traced_node 设的上下文自动带上；prompt 为模板文件名 + 模板内容哈希（不含案件内容）
            run_log.log_event(
                "llm_call",
                model=model,
                attempt=attempt,
                finish_reason=choice.finish_reason,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                duration_ms=round((time.monotonic() - t0) * 1000),
                prompt=run_log.current_prompt(),
            )

            # 推理模型可能把 max_tokens 全部耗在思维链上（finish_reason=length），
            # 导致正文为空——这是失败而非成功，必须走重试路径
            if not content.strip():
                if attempt == 1:
                    raise LLMEmptyResponse(
                        f"AI 返回内容为空（finish_reason={choice.finish_reason}），"
                        "已重试一次仍为空，请稍后重试"
                    )
                continue

            _served_model.set(model)
            if not json_mode:
                return content

            # JSON 模式：去除可能的 markdown 包裹，再解析、剥外壳（JSONDecodeError 往下走重试路径）
            cleaned = _strip_markdown_json(content)
            return _parse_json_content(cleaned)

        except (openai.AuthenticationError, openai.PermissionDeniedError):
            raise  # key 无效 / 无权限：重试无意义，由调用方决定换 key 或报错
        except TimeoutError as e:
            raise _Unavailable(e) from e  # 超过总时限：同 SDK 超时，按不可用处理
        except json.JSONDecodeError as e:
            # JSON 解析失败：第一次时重试，第二次时报错并附上原始片段
            last_exc = e
            if attempt == 1:
                raise RuntimeError(
                    f"AI 返回格式异常，已重试一次仍无法解析。原始内容片段：{last_content[:200]}"
                ) from e
        except RuntimeError:
            raise
        except Exception as e:
            if _is_unavailable(e):
                # 超时 / 连不上 / 5xx / 限流 / 欠费：不在同一服务商上再等一轮，直接交给调用方
                raise _Unavailable(e) from e
            last_exc = e
            # 第一次失败后继续重试，第二次失败则抛出
            if attempt == 1:
                raise _translate_error(e) from e

    raise _translate_error(last_exc or RuntimeError("未知错误"))


# ── 备用模型 ──────────────────────────────────────────────────────────────────
_fallback_clients: dict[str, AsyncOpenAI] = {}
# DeepSeek 出错后到这个时间点（monotonic 秒）之前都直接用备用模型；进程内有效
_primary_down_until: float = 0.0


def _fallback_keys() -> list[str]:
    return [k.strip() for k in (DASHSCOPE_API_KEY, DASHSCOPE_API_KEY_BACKUP) if k and k.strip()]


def _fallback_ready() -> bool:
    return bool(LLM_FALLBACK_MODEL) and bool(_fallback_keys())


def _get_fallback_client(key: str) -> AsyncOpenAI:
    client = _fallback_clients.get(key)
    if client is None:
        client = AsyncOpenAI(
            api_key=key, base_url=LLM_FALLBACK_BASE_URL, timeout=LLM_TIMEOUT, max_retries=0
        )
        _fallback_clients[key] = client
    return client


async def _chat_fallback(messages: list[Message], json_mode: bool) -> Any:
    """用备用模型完成调用；主 key 被拒（401/403）时换备用 key，与 OCR 一致。"""
    extra = {"enable_thinking": LLM_FALLBACK_ENABLE_THINKING}
    last: Exception | None = None
    for key in _fallback_keys():
        try:
            return await _complete(
                _get_fallback_client(key), LLM_FALLBACK_MODEL, messages, json_mode, extra
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            last = e
            continue
        except _Unavailable as u:
            raise RuntimeError(
                f"AI 服务暂不可用：DeepSeek 与备用模型 {LLM_FALLBACK_MODEL} 都调用失败"
                f"（{_translate_error(u.exc)}），请稍后重试"
            ) from u.exc
    raise RuntimeError(
        "DeepSeek 暂不可用，备用模型的 DashScope key 也被拒绝，请检查 .env 中的 DASHSCOPE_API_KEY"
    ) from last


async def chat(
    messages: list[Message],
    json_mode: bool = False,
) -> Any:
    """
    调用 DeepSeek API，失败自动重试一次；DeepSeek 不可用时改用备用模型（LLM_FALLBACK_MODEL）。

    Args:
        messages: 消息列表，格式 [{"role": "user", "content": "..."}]
        json_mode: True 时请求 JSON 输出，自动去除 markdown 包裹后解析为 dict 返回；
                   False 时返回原始字符串。

    Returns:
        json_mode=False → str
        json_mode=True  → dict[str, Any]

    Raises:
        RuntimeError: 含中文说明的错误（401 / 429 / 超时 / 其他）
    """
    global _primary_down_until

    if json_mode and not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": _JSON_SYSTEM_PROMPT}, *messages]
    _served_model.set(None)

    if _fallback_ready() and time.monotonic() < _primary_down_until:
        return await _chat_fallback(messages, json_mode)

    try:
        return await _complete(_get_client(), DEEPSEEK_MODEL, messages, json_mode)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        raise _translate_error(e) from e  # key 无效属配置错误：不切换，提示去改 .env
    except _Unavailable as u:
        if not _fallback_ready():
            raise _translate_error(u.exc) from u.exc
        _primary_down_until = time.monotonic() + LLM_FALLBACK_COOLDOWN_S
        logger.warning(
            "DeepSeek 不可用（%s），%d 分钟内改用备用模型 %s",
            type(u.exc).__name__,
            LLM_FALLBACK_COOLDOWN_S // 60,
            LLM_FALLBACK_MODEL,
        )
        run_log.log_event("llm_fallback", reason=type(u.exc).__name__, model=LLM_FALLBACK_MODEL)
        return await _chat_fallback(messages, json_mode)
