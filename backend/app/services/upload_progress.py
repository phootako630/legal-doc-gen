# 上传解析进度的内存态记录，供前端在上传期间轮询展示真实进度
#
# v1 是单进程、单用户场景，用模块级全局变量即可；将来支持多用户并发上传时，
# 需要改为按任务 ID 隔离的进度表（上传接口返回 task_id，进度接口按 id 查询）。
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()

_state: dict[str, Any] = {
    "active": False,       # 是否有上传任务正在处理
    "filename": None,      # 当前正在处理的文件名
    "file_index": 0,       # 当前文件序号（从 1 开始）
    "total_files": 0,      # 本次上传的文件总数
    "stage": "",           # '解析中' | 'OCR识别中'
    "done_pages": 0,       # OCR 已完成页数（并发完成，只增计数，不保证顺序）
    "total_pages": 0,      # OCR 总页数（0 表示当前文件不需要 OCR）
}


def begin(total_files: int) -> None:
    """一次上传请求开始时调用，重置所有进度。"""
    with _lock:
        _state.update(
            active=True, filename=None, file_index=0, total_files=total_files,
            stage="", done_pages=0, total_pages=0,
        )


def start_file(filename: str, file_index: int) -> None:
    """开始处理第 file_index 个文件（从 1 开始）。"""
    with _lock:
        _state.update(
            filename=filename, file_index=file_index,
            stage="解析中", done_pages=0, total_pages=0,
        )


def start_ocr(total_pages: int) -> None:
    """当前文件进入 OCR 阶段（仅扫描件会走到这里）。"""
    with _lock:
        _state.update(stage="OCR识别中", done_pages=0, total_pages=total_pages)


def page_done() -> None:
    """OCR 完成一页时由线程池工作线程调用，必须加锁防止并发丢计数。"""
    with _lock:
        _state["done_pages"] += 1


def finish() -> None:
    """一次上传请求处理结束（无论成败）时调用。"""
    with _lock:
        _state.update(active=False, stage="", filename=None)


def snapshot() -> dict[str, Any]:
    """返回当前进度的副本，供进度查询接口使用。"""
    with _lock:
        return dict(_state)
