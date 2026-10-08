# 原告信息表查询：按原告名称取统一社会信用代码、法定代表人 / 负责人、住址。
#
# 表由管理员在「原告信息表」页面上传维护（读写与格式见 branch_table.py），
# 这里只读当前生效版本。名称比对前统一去空白、半角括号转全角——律师表里有的行写
# 「日立电梯(中国)有限公司珠海分公司」，而系统拼出的原告名称用全角括号。
# 先按全称精确匹配；早期手写表里分公司只写简称（「江苏分公司」），再按「以简称结尾」匹配。
# 表不存在或查不到时返回 None，调用方保持字段缺失（起诉状留【待补充】），不猜。
from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import BRANCH_INFO_PATH
from app.services.branch_table import BranchTable, load_table, normalize_name


@dataclass(frozen=True)
class PartyInfo:
    """原告主体的固定信息（来自原告信息表）。"""

    credit_code: str | None
    person_in_charge: str | None
    address: str | None
    version: str | None = None  # 表的更新日期（YYYY-MM-DD），写进字段来源便于追溯


# 按 (路径, 修改时间) 缓存：管理员上传新表后文件 mtime 变化，下次查询自动读新版
_cache: dict[str, tuple[float, BranchTable | None]] = {}


def _current(path: str) -> BranchTable | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    table = load_table(path)
    _cache[path] = (mtime, table)
    return table


def _find(name: str | None, path: str | None) -> tuple[dict, BranchTable] | None:
    """全称精确匹配优先，其次按「以简称结尾」匹配（早期表里分公司只写简称）。"""
    if not name:
        return None
    table = _current(path or BRANCH_INFO_PATH)
    if not table:
        return None
    name = normalize_name(name)
    match = next((e for e in table.entries if e["name"] == name), None)
    if match is None:
        match = next(
            (e for e in table.entries if e["name"] and name.endswith(e["name"])), None
        )
    return (match, table) if match else None


def resolve_plaintiff_name(short_name: str | None, path: str | None = None) -> str | None:
    """
    审批表里写的公司名 → 信息表中的全称（审批表可能只写后半段，如「杭州工程有限公司」
    → 表中「日立电梯（中国）有限公司杭州工程有限公司」）。
    精确匹配，或表中有且只有一个全称以它结尾；否则返回 None（不猜）。
    """
    if not short_name:
        return None
    table = _current(path or BRANCH_INFO_PATH)
    if not table:
        return None
    name = normalize_name(short_name)
    if any(e["name"] == name for e in table.entries):
        return name
    hits = [e["name"] for e in table.entries if e["name"].endswith(name)]
    return hits[0] if len(hits) == 1 else None


def lookup_plaintiff(
    plaintiff_name: str | None, path: str | None = None
) -> PartyInfo | None:
    """按原告全称查信息表：全称精确匹配优先，其次按「以简称结尾」匹配。"""
    found = _find(plaintiff_name, path)
    if found is None:
        return None
    match, table = found
    return PartyInfo(
        match["credit_code"] or None,
        match["representative"] or None,
        match["address"] or None,
        (table.updated_at or "")[:10] or None,
    )
