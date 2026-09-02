# ④ 起诉状模板渲染：结构化字段 → 起诉状全文（确定性填槽，非 LLM 自由生成）
#
# 设计意图（对应 CLAUDE.md「模板渲染，不自由生成」）：
#   起诉状是固定法律格式，由结构化字段 + 模板确定性渲染，LLM 不写全文。
#   标注规则由代码按字段 status 贴，稳定可控：
#     value 为空       → 【待补充】
#     冲突字段         → 【高亮冲突：<值>】
#     OCR/扫描来源     → ⚠️ 待核实：<值>
#     正常             → 直接写入
#   金额同时输出阿拉伯数字与人民币大写；日期统一 XXXX年XX月XX日。
#   冲突状态由本模块重跑 validators.run_all_checks 自行判定（律师改后即时反映），
#   不依赖前端传入，保证与审核页一致。
from __future__ import annotations

import re

from app.services.prompt_loader import load_prompt
from app.services.validators import parse_amount, parse_date, run_all_checks

# ── 金额 → 人民币大写 ─────────────────────────────────────────────────────────
_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_SMALL_UNITS = ["", "拾", "佰", "仟"]  # 个/十/百/千
_SECTION_UNITS = ["", "万", "亿", "兆", "京"]  # 每 4 位一节


def _four_to_chinese(section: int) -> str:
    """把 0–9999 的一节转成大写（内部零折叠为单个「零」，无首尾多余零）。"""
    if section == 0:
        return ""
    digits = [
        (section // 1000) % 10,
        (section // 100) % 10,
        (section // 10) % 10,
        section % 10,
    ]  # 千 百 十 个
    result = ""
    pending_zero = False
    for i, d in enumerate(digits):
        unit = _SMALL_UNITS[3 - i]
        if d == 0:
            if result:  # 前面已有数字才可能需要补零
                pending_zero = True
        else:
            if pending_zero:
                result += "零"
                pending_zero = False
            result += _DIGITS[d] + unit
    return result


def _int_to_chinese(num: int) -> str:
    """非负整数转人民币大写（不含「元」）。"""
    if num == 0:
        return "零"
    sections: list[int] = []  # 由低位到高位，每节 4 位
    while num > 0:
        sections.append(num % 10000)
        num //= 10000

    parts: list[str] = []
    last_printed_idx: int | None = None
    for idx in range(len(sections) - 1, -1, -1):
        sec = sections[idx]
        if sec == 0:
            continue
        if last_printed_idx is not None:
            # 高节与低节之间：跨过整零节，或低节千位为 0（sec<1000），都需补一个「零」
            if last_printed_idx - idx > 1 or sec < 1000:
                parts.append("零")
        parts.append(_four_to_chinese(sec) + _SECTION_UNITS[idx])
        last_printed_idx = idx
    return "".join(parts)


def amount_to_chinese_rmb(amount: object) -> str | None:
    """
    金额转人民币大写，如 256266.8 → 贰拾伍万陆仟贰佰陆拾陆元捌角。
    解析失败返回 None。按分（两位小数）处理，规避浮点误差。
    """
    num = parse_amount(amount)
    if num is None:
        return None
    if num < 0:
        inner = amount_to_chinese_rmb(-num)
        return f"负{inner}" if inner else None

    cents = int(round(num * 100))
    yuan, jiao, fen = cents // 100, (cents // 10) % 10, cents % 10

    if cents == 0:
        return "零元整"

    result = _int_to_chinese(yuan) + "元" if yuan > 0 else ""
    if jiao == 0 and fen == 0:
        result += "整"
    else:
        if jiao > 0:
            result += _DIGITS[jiao] + "角"
        elif yuan > 0:
            result += "零"  # 元与分之间需补零
        if fen > 0:
            result += _DIGITS[fen] + "分"
    return result


# ── 单字段基础显示串（未贴状态标注前）───────────────────────────────────────
_AMOUNT_KEYS = {"total_amount", "paid_amount", "unpaid_amount"}
_DATE_KEYS = {"contract_sign_date", "acceptance_latest_date"}


def _format_arabic(num: float) -> str:
    """金额格式化：按分（两位小数）呈现后去掉多余的尾零与小数点。

    不用 f"{num:g}"——其默认 6 位有效数字会把 256266.8 误舍成 256267。
    """
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _base_display(key: str, value: object) -> str:
    """按字段类型生成基础显示串：金额附大写、日期规整、其余原样。"""
    if key in _AMOUNT_KEYS:
        num = parse_amount(value)
        if num is not None:
            return f"{_format_arabic(num)}元（人民币大写：{amount_to_chinese_rmb(num)}）"
    elif key in _DATE_KEYS:
        d = parse_date(value)
        if d is not None:
            return f"{d[0]}年{d[1]:02d}月{d[2]:02d}日"
    return str(value)


# ── 状态标注（代码按 status 贴，非 LLM）──────────────────────────────────────
_OCR_HINTS = ("OCR", "ocr", "扫描", "待核实")


def _conflict_field_keys(fields: dict) -> set[str]:
    """重跑确定性校验，收集处于冲突的字段 key（与审核页口径一致）。"""
    keys: set[str] = set()
    for c in run_all_checks(fields):
        if c.is_conflict:
            keys.update(c.related_fields)
            if c.key == "qty_consistency":
                keys.add("elevator_qty")  # 展示字段为最终台数
    return keys


def _annotate(key: str, fields: dict, conflict_keys: set[str]) -> str:
    """对单个占位符按字段状态返回带标注的显示串。"""
    node = fields.get(key)
    if isinstance(node, dict):
        value = node.get("value")
        src = str(node.get("src") or "")
    else:
        value, src = node, ""

    if value is None or (isinstance(value, str) and value.strip() == ""):
        return "【待补充】"  # 缺失：字面提示，留给律师手填

    base = _base_display(key, value)
    if key in conflict_keys:
        return f"【高亮冲突：{base}】"
    if any(hint in src for hint in _OCR_HINTS):
        return f"⚠️ 待核实：{base}"
    return base


# ── 主入口 ────────────────────────────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def render_complaint(fields: dict, template: str | None = None) -> str:
    """
    将律师确认的字段渲染为起诉状全文（确定性填槽 + 代码贴标注）。
    模板中未知/缺失的占位符（如法院名、落款日期）一律填 【待补充】。
    """
    if template is None:
        # load_prompt 不传变量时原样返回模板（占位符保留）
        template = load_prompt("complaint-template.md")

    conflict_keys = _conflict_field_keys(fields)

    def _repl(m: re.Match[str]) -> str:
        return _annotate(m.group(1), fields, conflict_keys)

    return _PLACEHOLDER_RE.sub(_repl, template).strip()
