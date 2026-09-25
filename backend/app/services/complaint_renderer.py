# ④ 起诉状模板渲染：结构化字段 → 起诉状全文（确定性填槽，非 LLM 自由生成）
#
# 设计意图（对应 CLAUDE.md「模板渲染，不自由生成」）：
#   起诉状是固定法律格式，由结构化字段 + 模板确定性渲染，LLM 不写全文。
#   标注规则由代码按字段 status 贴，稳定可控：
#     value 为空       → 【待补充】
#     冲突字段         → 【高亮冲突：<值>】
#     OCR/扫描来源     → ⚠️ 待核实：<值>
#     正常             → 直接写入
#   格式对齐律师诉状模板：金额只出阿拉伯数字（模板自带「¥…元」），日期写 2024年2月26日。
#   逾期利息标准、管辖法院辖区为派生字段（derived_fields），缺则渲染时补推。
#   冲突状态由本模块重跑 validators.run_all_checks 自行判定（律师改后即时反映），
#   不依赖前端传入，保证与审核页一致。
from __future__ import annotations

import re

from app.services.derived_fields import apply_derived_fields
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


# 条款摘录后接模板自带的「。」，去掉摘录末尾标点避免「。。」
_CLAUSE_TEXT_KEYS = {"payment_clause_text", "dispute_clause_text"}
_TRAILING_PUNCT = "。；;，,.、 \n"
# 句中出现的条目编号（「1. 进度款：…；2. 验收款：…」）：起诉状里是连贯的一句话，去掉编号
_ITEM_NO_RE = re.compile(r"(?:^|(?<=[。；;\n]))\s*\d{1,2}\s*[.、．]\s*(?!\d)")
_CJK = "\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef"
# 与汉字/中文标点相邻的空白（OCR 常插入：「总价的 20%」「30 个工作日」）
_SPACE_NEAR_CJK_RE = re.compile(f"(?<=[{_CJK}])\\s+|\\s+(?=[{_CJK}])")


def _tidy_clause(text: str) -> str:
    """条款摘录整理为起诉状正文里的一句话：去换行、去条目编号、去 OCR 插入的空格。"""
    text = _ITEM_NO_RE.sub("", text.strip())
    text = re.sub(r"\s*\n\s*", "", text)
    text = _SPACE_NEAR_CJK_RE.sub("", text)
    return text.rstrip(_TRAILING_PUNCT)


def _base_display(key: str, value: object) -> str:
    """按字段类型生成基础显示串：金额取数字（模板自带 ¥/元）、日期规整、条款去尾标点。"""
    if key in _AMOUNT_KEYS:
        num = parse_amount(value)
        if num is not None:
            return _format_arabic(num)
    elif key in _DATE_KEYS:
        d = parse_date(value)
        if d is not None:
            return f"{d[0]}年{d[1]}月{d[2]}日"
    elif key in _CLAUSE_TEXT_KEYS:
        return _tidy_clause(str(value))
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


def _contacts_display(contacts: object) -> str:
    """联系人列表 → 「张三 13800000000」，多人以「；」分隔；任一项来自 OCR 则整体标待核实。"""
    parts: list[str] = []
    uncertain = False
    for c in contacts if isinstance(contacts, list) else []:
        if not isinstance(c, dict):
            continue
        vals = []
        for sub in ("name", "phone"):
            node = c.get(sub)
            if isinstance(node, dict):
                v = node.get("value")
                if any(h in str(node.get("src") or "") for h in _OCR_HINTS):
                    uncertain = uncertain or v is not None
            else:
                v = node
            if v is not None and str(v).strip():
                vals.append(str(v).strip())
        if vals:
            parts.append(" ".join(vals))
    if not parts:
        return "【待补充】"
    text = "；".join(parts)
    return f"⚠️ 待核实：{text}" if uncertain else text


def _annotate(key: str, fields: dict, conflict_keys: set[str]) -> str:
    """对单个占位符按字段状态返回带标注的显示串。"""
    if key == "contacts":
        return _contacts_display(fields.get("contacts"))
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
    模板中未知/缺失的占位符一律填 【待补充】（落款日期由律师手写，模板留空）。
    """
    if template is None:
        # load_prompt 不传变量时原样返回模板（占位符保留）
        template = load_prompt("complaint-template.md")

    # 派生字段（利息标准/管辖法院）缺失时补推；在副本上做，不改调用方数据
    fields = apply_derived_fields(dict(fields))
    conflict_keys = _conflict_field_keys(fields)

    def _repl(m: re.Match[str]) -> str:
        return _annotate(m.group(1), fields, conflict_keys)

    return _PLACEHOLDER_RE.sub(_repl, template).strip()
