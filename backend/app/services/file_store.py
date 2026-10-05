# 上传文件原始字节的磁盘暂存：支持 agent 在 analyze 阶段按需对扫描件逐页 OCR。
#
# 设计意图（对应 CLAUDE.md「按需 OCR」）：
#   上传时不再整份 OCR 扫描件，只把原始 PDF 字节存到 UPLOAD_DIR，返回一个 file_id；
#   agent 在缺条款字段时凭 file_id 取回字节，对需要的页调用 ocr_page。
#   落盘（而非内存）使服务重启后仍可 resume；超过保留期的文件由 purge_expired 清除。
#   file_id 来自客户端请求，读取前必须校验格式，防止路径穿越。
#   内容是客户的完整合同原件：目录 0700、文件 0600（不依赖 umask），且读取时也按保留期判过期，
#   不依赖清理任务恰好跑过。
from __future__ import annotations

import os
import re
import time
import uuid

from app.config import CASE_RETENTION_DAYS, UPLOAD_DIR

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _ensure_dir() -> None:
    """建目录并收紧权限；目录已存在（含旧版本留下的）也一并改成 0700。"""
    os.makedirs(UPLOAD_DIR, mode=0o700, exist_ok=True)
    os.chmod(UPLOAD_DIR, 0o700)


def put(data: bytes) -> str:
    """存一份字节，返回其 file_id。先写临时文件再改名，避免读到写了一半的文件。"""
    _ensure_dir()
    file_id = uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, file_id)
    tmp = f"{path}.tmp"
    # 创建时就带 0600，避免先以 umask 默认权限落盘再改的空窗
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return file_id


def get(file_id: str | None) -> bytes | None:
    """取回字节；file_id 为空、格式不合法、已过期清除或不存在时返回 None（调用方据此优雅降级）。"""
    if not file_id or not _ID_RE.match(file_id):
        return None
    path = os.path.join(UPLOAD_DIR, file_id)
    try:
        if os.path.getmtime(path) < time.time() - CASE_RETENTION_DAYS * 86400:
            # 过期即视为不存在，并顺手删除，不等清理任务
            os.remove(path)
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def purge_expired(retention_days: float = CASE_RETENTION_DAYS) -> int:
    """删除超过保留期的文件，返回删除数量。目录不存在时视为无事可做。"""
    cutoff = time.time() - retention_days * 86400
    removed = 0
    try:
        names = os.listdir(UPLOAD_DIR)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(UPLOAD_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue  # 被并发删除或无权限：跳过，不影响主流程
    return removed


def clear() -> None:
    """清空全部暂存（测试用）。"""
    try:
        names = os.listdir(UPLOAD_DIR)
    except OSError:
        return
    for name in names:
        try:
            os.remove(os.path.join(UPLOAD_DIR, name))
        except OSError:
            pass
