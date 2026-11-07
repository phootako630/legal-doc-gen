# LLM / OCR 调用的请求哈希键：线上调用录制（call_recorder，#157）与 eval 回放缓存（scripts/case_eval/cache.py）
# 共用同一套键，线上录下的返回才能直接当 eval 的回放缓存用。改这里的算法会让所有已录制的缓存失效。
from __future__ import annotations

import hashlib
import json
from typing import Any

from app import config


def digest(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def llm_key(messages: list[dict], json_mode: bool) -> str:
    """调用方传给 chat 的消息（llm_client 补 JSON 系统指令之前）+ 主模型 + json_mode。"""
    return digest({"model": config.DEEPSEEK_MODEL, "messages": messages, "json": json_mode})


def ocr_key(img_b64: str) -> str:
    """OCR 模型 + 页面图片（PyMuPDF 确定性渲染，同一页每次相同）的哈希。"""
    img = hashlib.sha256(img_b64.encode("ascii")).hexdigest()
    return digest({"model": config.QWEN_OCR_MODEL, "image": img})
