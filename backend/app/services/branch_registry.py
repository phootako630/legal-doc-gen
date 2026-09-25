# 分公司信息表：按原告名称查分公司（或总公司）的统一社会信用代码、负责人、住址。
#
# 数据由律师提供（诉状中原告的这几项不在案件材料里），存为 JSON：
#   {
#     "headquarters": {"name": "日立电梯（中国）有限公司", "credit_code": "...",
#                      "person_in_charge": "...", "address": "..."},
#     "branches": [
#       {"name": "江苏分公司", "credit_code": "...", "person_in_charge": "王凯，总经理",
#        "address": "..."}
#     ]
#   }
# branches[].name 可写简称（「江苏分公司」）或全称（「日立电梯（中国）有限公司江苏分公司」）。
# 文件不存在或读不出时返回 None，调用方保持字段缺失（起诉状留【待补充】），不猜。
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from app.config import BRANCH_INFO_PATH


@dataclass(frozen=True)
class PartyInfo:
    """原告主体的固定信息（来自分公司信息表）。"""

    credit_code: str | None
    person_in_charge: str | None
    address: str | None


@lru_cache(maxsize=4)
def _load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _to_info(entry: dict) -> PartyInfo:
    def s(key: str) -> str | None:
        val = entry.get(key)
        return str(val).strip() or None if val is not None else None

    return PartyInfo(s("credit_code"), s("person_in_charge"), s("address"))


def lookup_plaintiff(
    plaintiff_name: str | None, path: str | None = None
) -> PartyInfo | None:
    """按原告全称查信息表：总公司全称精确匹配；分公司按全称或「以简称结尾」匹配。"""
    if not plaintiff_name:
        return None
    data = _load(path or BRANCH_INFO_PATH)
    if not data:
        return None
    name = plaintiff_name.strip()
    hq = data.get("headquarters")
    if isinstance(hq, dict) and str(hq.get("name") or "").strip() == name:
        return _to_info(hq)
    for entry in data.get("branches") or []:
        if not isinstance(entry, dict):
            continue
        short = str(entry.get("name") or "").strip()
        if short and (name == short or name.endswith(short)):
            return _to_info(entry)
    return None
