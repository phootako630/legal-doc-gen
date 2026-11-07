# 快照库（#157）的落盘工具：SNAPSHOT_DIR 下各级目录 0700、文件 0600（内容是案卷原文），
# 先写临时文件再改名，读的一方不会读到写了一半的文件。call_recorder 与 case_snapshot 共用。
from __future__ import annotations

import os
import threading

from app import config


def ensure_dir(path: str) -> None:
    """从 SNAPSHOT_DIR 起逐级建目录，每一级都设成 0700（makedirs 的 mode 只管最后一级）。"""
    root = os.path.abspath(config.SNAPSHOT_DIR)
    parts = os.path.relpath(os.path.abspath(path), root).split(os.sep)
    current = root
    for part in [""] + [p for p in parts if p not in ("", ".")]:
        current = os.path.join(current, part) if part else current
        if not os.path.isdir(current):
            os.makedirs(current, mode=0o700, exist_ok=True)
        if os.stat(current).st_mode & 0o777 != 0o700:
            os.chmod(current, 0o700)


def write_bytes(path: str, data: bytes) -> None:
    ensure_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def write_text(path: str, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))
