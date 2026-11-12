# 起诉状就绪度与「冲突字段」的口径：agent（nodes.validate_node）、案件 eval 与线上快照统计共用，
# 改一处全跟着变，免得几份实现悄悄分叉、报告出现伪变化（#163 评审）。
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.complaint_renderer import fill_status
from app.services.validators import ValidationCheck

Check = ValidationCheck | Mapping[str, Any]


def _get(check: Check, name: str) -> Any:
    return check.get(name) if isinstance(check, Mapping) else getattr(check, name, None)


def conflict_keys(checks: Iterable[Check]) -> set[str]:
    """
    处于冲突的字段 key（审核页按冲突显示，与前端 collectConflictKeys 一致）。
    台数三源不一致对应展示字段 elevator_qty（表格不单列三个来源口径）。
    checks 可以是 ValidationCheck，也可以是序列化后的字典（check_to_dict / 快照里存的形式）。
    """
    keys: set[str] = set()
    for c in checks or []:
        if not _get(c, "is_conflict"):
            continue
        keys.update(_get(c, "related_fields") or [])
        if _get(c, "key") == "qty_consistency":
            keys.add("elevator_qty")
    return keys


def readiness(fields: dict, checks: Iterable[Check]) -> int:
    """起诉状就绪度 0–100：模板里的填空中，有值且不冲突的占比。

    按模板实际填空统计（措辞类占位符不计），与预览页「N 处待补充」口径一致；
    原先只看 10 个固定字段，原告信用代码 / 负责人 / 住址、被告法定代表人 / 住址等缺失时仍显示 100%。
    """
    keys, missing = fill_status(fields)
    if not keys:
        return 0
    conflicted = conflict_keys(checks)
    good = sum(1 for k in keys if k not in missing and k not in conflicted)
    return round(good / len(keys) * 100)
