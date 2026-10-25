# 冲突断点的候选值（#70）：按校验项列出各字段可选的取值及来源，律师在审核页一键选用
#
# 只罗列材料里已有的值和据此能算出的值，不替律师选：
#   - 金额勾稽：三项的现值，以及用另外两项推出的值（总价 = 已付 + 未付 等）
#   - 台数三源：各来源的台数（相同台数合并来源）
#   - 信用代码 / 日期：现值及出处（律师核对原件后修改）
from __future__ import annotations

from app.services.validators import ValidationCheck, parse_amount, parse_qty

_AMOUNT_KEYS = ("total_amount", "paid_amount", "unpaid_amount")
_QTY_SOURCES = {
    "elevator_qty_by_approval": "审批表",
    "elevator_qty_by_contract": "合同",
    "elevator_qty_by_acceptance": "验收报告",
}


def _node(fields: dict, key: str) -> dict:
    node = fields.get(key)
    return node if isinstance(node, dict) else {"value": node}


def _amount_text(amount: float) -> str:
    """金额写成可直接回填的数字：整数不带小数，其余保留到分。"""
    rounded = round(amount, 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.2f}"


def _current(fields: dict, key: str) -> dict | None:
    node = _node(fields, key)
    value = node.get("value")
    if value is None or str(value).strip() == "":
        return None
    src = str(node.get("src") or "").strip() or "出处未注明"
    return {"value": str(value), "source": f"现值：{src}"}


def _add(options: dict[str, list[dict]], key: str, value: str, source: str) -> None:
    """按值去重：同一取值出现多次时合并来源说明。"""
    items = options.setdefault(key, [])
    for item in items:
        if item["value"] == value:
            if source not in item["source"]:
                item["source"] += f"；{source}"
            return
    items.append({"value": value, "source": source})


def _amount_options(fields: dict, options: dict[str, list[dict]]) -> None:
    t, p, u = (parse_amount(_node(fields, k).get("value")) for k in _AMOUNT_KEYS)
    for key in _AMOUNT_KEYS:
        cur = _current(fields, key)
        if cur:
            # 现值照原样给出：与审核页输入框里的值一致，点选时能看出就是现值
            _add(options, key, cur["value"], cur["source"])
    if t is None or p is None or u is None:
        return
    derived = (
        ("total_amount", p + u, "按已付 + 未付计算"),
        ("paid_amount", t - u, "按总价 − 未付计算"),
        ("unpaid_amount", t - p, "按总价 − 已付计算"),
    )
    # 冲突时三项不勾稽，推算值必与现值不同（差额超过一分）
    for key, amount, how in derived:
        if amount >= 0:
            _add(options, key, _amount_text(amount), how)


def _qty_options(fields: dict, options: dict[str, list[dict]]) -> None:
    candidates = []
    for key, name in _QTY_SOURCES.items():
        qty = parse_qty(_node(fields, key).get("value"))
        if qty is not None:
            candidates.append((str(qty), f"{name}写 {qty} 台"))
    # 写进起诉状的台数与三个来源字段都可从这几个口径里选
    for key in (*_QTY_SOURCES, "elevator_qty"):
        for value, source in candidates:
            _add(options, key, value, source)


def conflict_options(
    checks: list[ValidationCheck], fields: dict
) -> dict[str, list[dict]]:
    """为冲突校验项涉及的字段列出候选值：{字段 key: [{value, source}]}。"""
    options: dict[str, list[dict]] = {}
    for check in checks:
        if check.key == "amount_reconcile":
            _amount_options(fields, options)
        elif check.key == "qty_consistency":
            _qty_options(fields, options)
        else:
            for key in check.related_fields:
                cur = _current(fields, key)
                if cur:
                    _add(options, key, cur["value"], cur["source"])
    return options
