# DeepSeek API 封装：基于 OpenAI SDK，统一 LLM 调用入口
# chat(messages, json_mode=False) → str
# chat(messages, json_mode=True)  → dict（自动去除 markdown 包裹后解析）
from __future__ import annotations

import json
import re
from typing import Any

import openai
from openai import AsyncOpenAI

from app.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
)

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
        )
    return _client


def _strip_markdown_json(raw: str) -> str:
    """去除 LLM 返回内容中可能包裹的 ```json ... ``` 标记。"""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _translate_error(exc: Exception) -> RuntimeError:
    """将 OpenAI SDK 异常转换为带中文说明的 RuntimeError。"""
    if isinstance(exc, openai.AuthenticationError):
        return RuntimeError("API Key 无效，请检查 .env 中的 DEEPSEEK_API_KEY")
    if isinstance(exc, openai.RateLimitError):
        return RuntimeError("请求过于频繁，请稍后重试")
    if isinstance(exc, openai.APITimeoutError):
        return RuntimeError("AI 处理超时，请稍后重试或缩短输入长度")
    if isinstance(exc, openai.APIConnectionError):
        return RuntimeError("无法连接到 AI 服务，请检查网络连接")
    return RuntimeError(f"AI 调用失败：{exc}")


async def chat(
    messages: list[Message],
    json_mode: bool = False,
) -> Any:
    """
    调用 DeepSeek API，失败自动重试一次。

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
    client = _get_client()

    kwargs: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None
    last_content: str = ""  # 保留最近一次原始返回，用于 JSON 解析失败时的调试信息

    for attempt in range(2):  # 最多尝试 2 次
        try:
            response = await client.chat.completions.create(**kwargs)
            content: str = response.choices[0].message.content or ""
            last_content = content

            if not json_mode:
                return content

            # JSON 模式：去除可能的 markdown 包裹，再解析（JSONDecodeError 往下走重试路径）
            cleaned = _strip_markdown_json(content)
            return json.loads(cleaned)

        except (
            openai.AuthenticationError,
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
        ) as e:
            # 这类错误重试无意义，直接转换为中文错误
            raise _translate_error(e) from e
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
            last_exc = e
            # 第一次失败后继续重试，第二次失败则抛出
            if attempt == 1:
                raise _translate_error(e) from e

    raise _translate_error(last_exc or RuntimeError("未知错误"))
