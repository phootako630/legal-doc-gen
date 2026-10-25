# 就绪检查（/readyz）的各项检查：磁盘剩余空间、API Key 是否已配置（#76）
#
# 只做本地检查，不调外部接口（Key 的连通性留给每日小结），探活频繁也不花钱、不受外部服务波动影响。
# 结果只有检查项名称、是否通过和中文说明，不含路径以外的任何案件信息。
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _existing_dir(path: str) -> str:
    """目录可能还没建（首次上传前）：往上找到第一个存在的目录来量剩余空间。"""
    cur = os.path.abspath(path)
    while not os.path.isdir(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur


def check_disk(name: str, path: str, min_free_mb: int) -> Check:
    """path 所在磁盘剩余空间不少于 min_free_mb（MB）。"""
    try:
        free_mb = shutil.disk_usage(_existing_dir(path)).free // (1024 * 1024)
    except OSError as exc:
        return Check(name, False, f"读不出剩余空间（{type(exc).__name__}）")
    if free_mb < min_free_mb:
        return Check(name, False, f"剩余 {free_mb} MB，低于 {min_free_mb} MB")
    return Check(name, True, f"剩余 {free_mb} MB")


def check_key(name: str, value: str | None) -> Check:
    """只看是否已配置，不打印 key、不调接口。"""
    if value and value.strip():
        return Check(name, True, "已配置")
    return Check(name, False, "未配置")


def summarize(checks: list[Check]) -> dict:
    return {"ready": all(c.ok for c in checks), "checks": [asdict(c) for c in checks]}
