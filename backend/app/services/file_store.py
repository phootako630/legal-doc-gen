# 上传文件原始字节的内存暂存：支持 agent 在 analyze 阶段按需对扫描件逐页 OCR。
#
# 设计意图（对应 CLAUDE.md「按需 OCR」）：
#   上传时不再整份 OCR 扫描件，只把原始 PDF 字节暂存于此，返回一个 file_id；
#   agent 在缺条款字段时凭 file_id 取回字节，对需要的页调用 ocr_page。
#   与 upload_progress / llm_progress 同思路：v1 单进程单用户，模块级全局字典即可；
#   将来多用户/持久化时替换为带 TTL 的存储或对象存储。
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

# 最多保留的文件数（防止长时间运行内存无上限增长）；超出按最早插入淘汰
_MAX_FILES = 64

_lock = threading.Lock()
_store: "OrderedDict[str, bytes]" = OrderedDict()


def put(data: bytes) -> str:
    """暂存一份字节，返回其 file_id。超过上限时淘汰最早的一份。"""
    file_id = uuid.uuid4().hex
    with _lock:
        _store[file_id] = data
        _store.move_to_end(file_id)
        while len(_store) > _MAX_FILES:
            _store.popitem(last=False)
    return file_id


def get(file_id: str | None) -> bytes | None:
    """取回字节；file_id 为空或已淘汰/不存在时返回 None（调用方据此优雅降级）。"""
    if not file_id:
        return None
    with _lock:
        return _store.get(file_id)


def clear() -> None:
    """清空暂存（测试或会话结束时用）。"""
    with _lock:
        _store.clear()
