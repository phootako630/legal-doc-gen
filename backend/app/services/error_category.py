# 错误分类：把节点、OCR 里抛出的异常归到固定类别，运行日志按类别统计「错在哪一层」（#75）
#
# 只看异常类型和异常链（__cause__ / __context__），类别是固定的几个英文标识，不记异常原文
# （原文可能夹带案件内容）。确定性校验「不通过」是业务结论，不是错误，不走这里。
from __future__ import annotations

import json

import openai
import requests

OCR_TIMEOUT = "ocr_timeout"
OCR_RATE_LIMIT = "ocr_rate_limit"
OCR_FAILED = "ocr_failed"
LLM_TIMEOUT = "llm_timeout"
LLM_RATE_LIMIT = "llm_rate_limit"
LLM_EMPTY = "llm_empty"  # finish_reason=length 等原因正文为空，重试仍空
LLM_BAD_JSON = "llm_bad_json"
PARSE_UNSUPPORTED = "parse_unsupported"
INTERNAL = "internal"

CATEGORIES = (
    OCR_TIMEOUT, OCR_RATE_LIMIT, OCR_FAILED, LLM_TIMEOUT, LLM_RATE_LIMIT,
    LLM_EMPTY, LLM_BAD_JSON, PARSE_UNSUPPORTED, INTERNAL,
)


class LLMEmptyResponse(RuntimeError):
    """LLM 返回正文为空（重试后仍空）。单独成类，便于归类，不靠匹配中文提示。"""


class UnsupportedFileError(ValueError):
    """上传的文件格式不支持，或内容为空、提取不出文本。"""


def _chain(exc: BaseException) -> list[BaseException]:
    """异常本身及其成因链（含 llm_client._Unavailable 包着的 .exc），防环。"""
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        cur = todo.pop(0)
        if cur is None or any(cur is s for s in seen):
            continue
        seen.append(cur)
        todo += [cur.__cause__, cur.__context__, getattr(cur, "exc", None)]
    return seen


def _status(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def categorize(exc: BaseException) -> str:
    """异常 → 固定类别。先认最具体的成因（限流 / 超时 / 空返回 / 坏 JSON），认不出归 internal。"""
    chain = _chain(exc)
    is_ocr = any(isinstance(e, requests.RequestException) for e in chain) or any(
        isinstance(e, (TimeoutError, RuntimeError)) and "OCR" in str(e) for e in chain
    )
    for e in chain:
        if isinstance(e, LLMEmptyResponse):
            return LLM_EMPTY
        if isinstance(e, json.JSONDecodeError):
            return LLM_BAD_JSON
        if isinstance(e, UnsupportedFileError):
            return PARSE_UNSUPPORTED
        if isinstance(e, openai.RateLimitError):
            return LLM_RATE_LIMIT
        if isinstance(e, openai.APITimeoutError):
            return LLM_TIMEOUT
        if isinstance(e, requests.HTTPError) and _status(e) == 429:
            return OCR_RATE_LIMIT
        if isinstance(e, requests.Timeout):
            return OCR_TIMEOUT
    if any(isinstance(e, TimeoutError) for e in chain):
        return OCR_TIMEOUT if is_ocr else LLM_TIMEOUT
    if is_ocr:
        return OCR_FAILED
    return INTERNAL
