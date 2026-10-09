# 上传解析进度（逐文件解析 / OCR 页数），按 progress_id 隔离，供前端上传期间轮询
#
# 路由用 track(progress_id, total_files) 包住一次上传。注意：整份 OCR 的旧路径在线程池里
# 调用 start_ocr / page_done，contextvar 不会进入线程，届时这两个调用为空操作——
# 该路径自按需 OCR（第 6b 步）后已不再被上传调用。
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.services.progress_table import ProgressTable


def _idle() -> dict[str, Any]:
    return {
        "filename": None,  # 当前正在处理的文件名
        "file_index": 0,  # 当前文件序号（从 1 开始）
        "total_files": 0,  # 本次上传的文件总数
        "stage": "",  # '解析中' | 'OCR识别中'
        "done_pages": 0,  # OCR 已完成页数
        "total_pages": 0,  # OCR 总页数（0 表示当前文件不需要 OCR）
    }


_table = ProgressTable("upload_progress", _idle)


@contextmanager
def track(task_id: str | None, total_files: int) -> Iterator[None]:
    """包住一次上传请求：with 块内的进度更新记到 task_id 名下，结束自动复位。"""
    with _table.track(task_id, total_files=total_files):
        yield


def start_file(filename: str, file_index: int) -> None:
    """开始处理第 file_index 个文件（从 1 开始）。"""
    _table.update(
        filename=filename, file_index=file_index, stage="解析中", done_pages=0, total_pages=0
    )


def start_ocr(total_pages: int) -> None:
    """当前文件进入 OCR 阶段。"""
    _table.update(stage="OCR识别中", done_pages=0, total_pages=total_pages)


def page_done() -> None:
    """OCR 完成一页。"""
    _table.increment("done_pages")


def snapshot(task_id: str | None) -> dict[str, Any]:
    """某次上传的当前进度；未知/已结束任务返回非活跃默认值。"""
    return _table.snapshot(task_id)
