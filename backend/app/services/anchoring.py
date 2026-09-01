# 值回原文命中/定位：把抽取值拿回来源文本里精确比对，反幻觉的硬信号来源
#
# 设计意图（对应 CLAUDE.md「出处可追溯 / 宁缺勿造」）：
#   LLM 抽出的每个 value 必须能在原文里命中，否则一律判为幻觉并置空。
#   这里只做确定性的字符串定位，不调用任何 LLM。匹配分三档：
#     exact —— 归一化后原文包含该值（可直接高亮跳转）
#     fuzzy —— 去掉空白/标点、或按金额/日期等价形式才命中（需降权，可能有噪声）
#     none  —— 完全找不到（→ 反幻觉置空）
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

AnchorQuality = Literal["exact", "fuzzy", "none"]


@dataclass(frozen=True)
class AnchorResult:
    """单次锚定结果。position 为归一化文本中的字符下标，仅用于粗定位。"""

    hit: bool
    quality: AnchorQuality
    matched_text: str | None = None
    position: int | None = None


# ── 文本归一化 ────────────────────────────────────────────────────────────────
# 统一全角/半角、大小写、连续空白，降低"看起来一样但字节不同"导致的漏配。
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """NFKC 归一（全角→半角等）+ 折叠连续空白，不改变可见字符序。"""
    norm = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", norm).strip()


def _strip_all_ws(text: str) -> str:
    """去掉所有空白字符，用于 fuzzy 阶段的宽松包含判断。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


# ── 金额等价匹配 ──────────────────────────────────────────────────────────────
# 抽取值可能是 894934，而原文写作 "894,934.00 元" / "￥894934"，需数值等价而非字面。
_AMOUNT_TOKEN_RE = re.compile(r"\d[\d,\s]*(?:\.\d+)?")


def _to_number(raw: str) -> float | None:
    """把带千分位/货币符号/单位的字符串转成数值，失败返回 None。"""
    cleaned = re.sub(r"[，,\s]", "", unicodedata.normalize("NFKC", str(raw)))
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _numbers_equal(a: float, b: float) -> bool:
    """金额按分（0.01）容差比较，规避浮点误差。"""
    return abs(a - b) <= 0.01


def _match_amount(value_num: float, source: str) -> int | None:
    """在原文里扫描所有数字 token，命中数值相等者返回其位置。"""
    for m in _AMOUNT_TOKEN_RE.finditer(source):
        token_num = _to_number(m.group())
        if token_num is not None and _numbers_equal(token_num, value_num):
            return m.start()
    return None


# ── 日期等价匹配 ──────────────────────────────────────────────────────────────
# "2023年5月1日" 与 "2023-05-01" / "2023/5/1" 等价，抽取值与原文写法常不一致。
_DATE_RE = re.compile(
    r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?"
)


def _date_key(raw: str) -> tuple[int, int, int] | None:
    """把任意常见写法的日期归一成 (年, 月, 日)，失败返回 None。"""
    m = _DATE_RE.search(unicodedata.normalize("NFKC", str(raw)))
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return (year, month, day)


def _match_date(value_key: tuple[int, int, int], source: str) -> int | None:
    """在原文里扫描所有日期，命中同一 (年,月,日) 者返回其位置。"""
    norm = unicodedata.normalize("NFKC", source)
    for m in _DATE_RE.finditer(norm):
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if key == value_key:
            return m.start()
    return None


# ── 主入口 ────────────────────────────────────────────────────────────────────
def anchor_value(value: str | int | float | None, source_text: str) -> AnchorResult:
    """
    判断抽取值 value 是否能在 source_text 里命中，并给出命中质量。

    命中顺序（先严后宽，取第一个成功者）：
      1. 归一化后精确子串包含        → exact
      2. 金额数值等价 / 日期等价      → exact（语义等价视为精确命中）
      3. 去空白后包含                → fuzzy
    全部失败 → none（调用方据此反幻觉置空）。
    空值 / 空文本一律 none。
    """
    if value is None or source_text is None:
        return AnchorResult(hit=False, quality="none")

    raw_value = str(value).strip()
    if raw_value == "" or source_text.strip() == "":
        return AnchorResult(hit=False, quality="none")

    norm_source = _normalize(source_text)
    norm_value = _normalize(raw_value)

    # 1) 精确子串
    pos = norm_source.find(norm_value)
    if pos != -1:
        return AnchorResult(True, "exact", norm_value, pos)

    # 2) 金额数值等价
    value_num = _to_number(raw_value)
    if value_num is not None:
        pos = _match_amount(value_num, norm_source)
        if pos is not None:
            return AnchorResult(True, "exact", raw_value, pos)

    # 3) 日期等价
    date_key = _date_key(raw_value)
    if date_key is not None:
        pos = _match_date(date_key, norm_source)
        if pos is not None:
            return AnchorResult(True, "exact", raw_value, pos)

    # 4) 去空白宽松包含
    stripped_source = _strip_all_ws(source_text)
    stripped_value = _strip_all_ws(raw_value)
    if stripped_value and stripped_value in stripped_source:
        return AnchorResult(True, "fuzzy", raw_value, stripped_source.find(stripped_value))

    return AnchorResult(hit=False, quality="none")
