# 线上调用录制（#157）：每次 LLM（DeepSeek 主模型）与 OCR 调用的返回，按与 eval 回放缓存相同的请求哈希键、
# 相同的文件格式存进快照库：
#   <SNAPSHOT_DIR>/calls/llm/<键>.json   {"result": 返回}
#   <SNAPSHOT_DIR>/calls/ocr/<键>.txt    识别文本
# 导出 eval 案卷时，这里就是 ReplayCache 的缓存目录：严格回放读到的就是律师当时看到的结果。
#
# - 开关 SNAPSHOT_ENABLED（默认关）；备用模型的返回、空的 OCR 结果不录（与 eval 录制一致）。
# - 内容是案卷原文：与案件数据同在数据卷、同一套保密要求，只是保留期更长（SNAPSHOT_RETENTION_DAYS，
#   默认 180 天，律所同意），超期由服务的定时清理循环删除；同一请求再次出现会刷新修改时间。
# - 录制失败只记一条不含内容的警告，绝不影响主流程。
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app import config
from app.services.call_keys import llm_key, ocr_key

logger = logging.getLogger(__name__)


def calls_dir() -> str:
    return os.path.join(config.SNAPSHOT_DIR, "calls")


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _record(kind: str, key: str, ext: str, text: str) -> None:
    try:
        _write(os.path.join(calls_dir(), kind, f"{key}.{ext}"), text)
    except Exception as exc:  # noqa: BLE001 — 录制是尽力而为，不能影响律师在用的流程
        logger.warning("调用录制失败（%s）：%s", kind, type(exc).__name__)


def record_llm(messages: list[dict], json_mode: bool, result: Any) -> None:
    """DeepSeek 主模型的一次成功返回。messages 须是调用方传入的原样（补 JSON 系统指令之前）。"""
    if not config.SNAPSHOT_ENABLED:
        return
    try:
        key, text = llm_key(messages, json_mode), json.dumps({"result": result}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("调用录制失败（llm）：%s", type(exc).__name__)
        return
    _record("llm", key, "json", text)


def record_ocr(img_b64: str, text: str) -> None:
    """一页 OCR 的识别文本；空串多半是接口异常，不录。"""
    if not config.SNAPSHOT_ENABLED or not text:
        return
    _record("ocr", ocr_key(img_b64), "txt", text)


def purge_old(retention_days: float | None = None) -> int:
    """删除修改时间早于保留期的录制文件，返回删除个数。"""
    days = config.SNAPSHOT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = time.time() - days * 86400
    removed = 0
    for kind in ("llm", "ocr"):
        folder = os.path.join(calls_dir(), kind)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
