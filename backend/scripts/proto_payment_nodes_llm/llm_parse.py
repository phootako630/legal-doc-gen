# 【原型，用完即弃】付款节点：LLM 给出结构，代码只核对与版式无关的不变量，核不过就返回空（同现有正则的退回口径）。
#
# 不变量：① 每个节点的原文片段在条款原文里逐字命中（只忽略空白）；② 编号在原文里、位于本片段与前一个片段之间；
# ③ 比例写法逐字出现在片段里，且由代码解析出的数值就是比例（模型报的数不用）；④ 片段互不重叠；
# ⑤ 代码累加：本期 + 前面累计 / 累计取本身，单调不减、不超过且最终等于 100%；
# ⑥ 原文里每个百分比都有着落：是某节点的比例 / 分次付款（分次合计须等于该节点比例），或落在排除项片段里。
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.payment_nodes import PaymentStage

_TOL = 0.05

PROMPT = """下面是一份中国电梯安装 / 买卖合同的付款条款原文。请把它拆成甲方（买方 / 发包方）的付款节点。

只列真实的付款义务：每个节点是甲方在某个时点支付合同价款的一部分。下列不是付款节点，放进 excluded：
乙方开具 / 提供的保函本身、违约金 / 利息 / 滞纳金、抵扣或扣回条件（如「付到85%时抵扣」「扣回50%」）、
只是说明付款方式（电汇等）的句子、没有比例的笼统描述。

每个节点输出：
- no：该节点在原文中的编号，照抄（如「3.2」「4.2.1」「（一）」「①」「第一期」「1」）；没有编号写空串。
- quote：该节点的原文，从原文逐字连续照抄，必须包含比例写法；不要改字、不要补字、不要删中间的字。
- basis：「本期」（这一期付合同价的 X%）、「累计」（支付至 / 付至 / 累计达到 X%）、「付清」（付清余款 / 全部款项，没写比例）、「金额」（只写了金额没写比例）。
- pct_text：比例在 quote 里的原样写法，逐字照抄（如「30%」「百分之三十」「三成」「9 5 ％」）；basis 为「付清」时照抄触发词（如「付清全部余款」）。
- parts：这一期再分几次付清时，各次比例的原样写法（如 ["1.5%", "1.5%"]）；不分次写 []。

要求：
- 节点按付款先后排列（质保金 / 尾款一般最后付），不按原文出现顺序。
- 一期里先写「合同价的3%为质保金」再分几次付清的，算一个节点，pct_text 取「3%」，各次写进 parts。
- 原文里出现的每一个百分比（含「百分之…」「…成」），要么是某个节点的 pct_text / parts，要么在 excluded 的 quote 里（逐字照抄，写明理由）。
- 不要计算、不要推测原文没写的节点或比例；条款本身合计不到 100% 就如实列出，不要补。

输出 JSON：{"stages": [{"no": "", "quote": "", "basis": "", "pct_text": "", "parts": []}], "excluded": [{"quote": "", "reason": ""}]}

付款条款原文：
<<<
{text}
>>>"""

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# 原文里的百分比写法（去空白后）：30% / 百分之三十 / 三成
_PCT_ANY_RE = re.compile(r"\d+(?:\.\d+)?[%％]|百分之(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)|[一二两三四五六七八九十]成")
_CUM_WORD_RE = re.compile(r"至|支付到|付到|累计|达到")


def _has_token(hay: str, no: str) -> bool:
    """编号作为独立记号出现：前后不紧挨数字或小数点（「15日」里的「1」不算）。"""
    return re.search(rf"(?<!\d)(?<!\d[.．]){re.escape(no)}(?![\d%％]|[.．]\d)", hay) is not None


_PAYOFF_RE = re.compile(r"付清|全部|余款|剩余|余下|尾款|结清")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _cn_number(s: str) -> float | None:
    """百以内的中文数字：三十、九十五、十、十五、一。"""
    if not s or any(c not in _CN_DIGIT and c != "十" for c in s):
        return None
    if "十" not in s:
        return float(_CN_DIGIT[s]) if len(s) == 1 else None
    tens, _, ones = s.partition("十")
    t = _CN_DIGIT.get(tens, None) if tens else 1
    o = _CN_DIGIT.get(ones, None) if ones else 0
    return None if t is None or o is None else float(t * 10 + o)


def parse_pct(pct_text: str) -> float | None:
    s = _squash(pct_text)
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)[%％]", s):
        return float(m.group(1))
    if m := re.fullmatch(r"百分之(\d+(?:\.\d+)?)", s):
        return float(m.group(1))
    if m := re.fullmatch(r"百分之([零〇一二两三四五六七八九十]+)", s):
        return _cn_number(m.group(1))
    if m := re.fullmatch(r"([一二两三四五六七八九十])成", s):
        v = _cn_number(m.group(1))
        return None if v is None else v * 10
    return None


@dataclass
class Result:
    stages: list[PaymentStage]
    reason: str  # 放弃原因；成功为空
    raw: dict | None


def validate(text: str, raw: dict) -> Result:
    full = _squash(text)
    items = raw.get("stages") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        return Result([], "没有节点", raw)
    used: list[tuple[int, int]] = []
    claims: list[tuple[tuple[int, int], set[str], float]] = []
    span_no: dict[tuple[int, int], str] = {}
    stages: list[PaymentStage] = []
    running = 0.0
    for i, it in enumerate(items, 1):
        no, quote = _squash(str(it.get("no") or "")), _squash(str(it.get("quote") or ""))
        basis, pct_text = str(it.get("basis") or ""), _squash(str(it.get("pct_text") or ""))
        pos = full.find(quote) if quote else -1
        if pos < 0:
            return Result([], f"第{i}个节点原文未命中", raw)
        span = (pos, pos + len(quote))
        if any(a < span[1] and span[0] < b for a, b in used):
            return Result([], f"第{i}个节点与其他节点重叠", raw)
        used.append(span)
        prev = max(((a, b) for a, b in used[:-1] if b <= pos), key=lambda x: x[1], default=None)
        prev_end = prev[1] if prev else 0
        # 编号作为独立记号（前后不紧挨数字）出现在片段里（表格行）或本片段与前一片段之间
        if no and not (_has_token(quote, no) or _has_token(full[prev_end:pos], no)):
            return Result([], f"第{i}个节点编号「{no}」不在原文该处", raw)
        parts = [_squash(str(x)) for x in (it.get("parts") or [])]
        if parts:
            vals = [parse_pct(x) for x in parts]
            if any(v is None for v in vals) or any(x not in quote for x in parts):
                return Result([], f"第{i}个节点分次比例写法核不了", raw)
            if abs(sum(vals) - (parse_pct(pct_text) or -1)) > _TOL:
                return Result([], f"第{i}个节点分次合计{sum(vals):g}%不等于{pct_text}", raw)
        span_no[span] = no
        if pct_text not in quote:
            return Result([], f"第{i}个节点比例写法不在原文片段里", raw)
        if basis == "付清":
            v = parse_pct(pct_text)
            if v is not None:  # 「余款5%……付清」：比例须正好补足到 100%
                if abs(running + v - 100) > _TOL:
                    return Result([], f"第{i}个节点付清比例{v:g}%补不到100%", raw)
            elif not _PAYOFF_RE.search(pct_text):
                return Result([], f"第{i}个节点「付清」没有触发词", raw)
            end = 100.0
        elif basis in ("本期", "累计"):
            v = parse_pct(pct_text)
            if v is None:
                return Result([], f"第{i}个节点比例「{pct_text}」解析不出", raw)
            end = v if basis == "累计" else running + v
        else:
            return Result([], f"第{i}个节点依据为「{basis}」，核不了比例", raw)
        if end <= running + _TOL or end > 100 + _TOL:
            return Result([], f"第{i}个节点累计没有增加或超过100%", raw)
        # 本期 / 累计要有原文依据：累计须见「至 / 到 / 累计」（或表头写「累计」），本期的比例前不能紧挨「至 / 到」
        before = quote[: quote.find(pct_text)][-12:] if pct_text in quote else ""
        if basis == "累计" and not (_CUM_WORD_RE.search(quote) or "累计" in full[:pos]):
            return Result([], f"第{i}个节点标为累计，原文没有「至 / 累计」", raw)
        if basis == "本期" and _CUM_WORD_RE.search(before):
            return Result([], f"第{i}个节点标为本期，原文写的是「至」", raw)
        claims.append((span, {pct_text, *parts}, end))
        stages.append(PaymentStage(str(it.get("no") or ""), str(it.get("quote") or ""), running, end))
        running = end
    if abs(running - 100) > _TOL:
        return Result([], f"累计{running:g}%不到100%", raw)
    excluded = []
    for ex in raw.get("excluded") or []:
        q = _squash(str(ex.get("quote") or "")) if isinstance(ex, dict) else ""
        p = full.find(q) if q else -1
        if p >= 0:
            excluded.append((p, p + len(q)))
    for m in _PCT_ANY_RE.finditer(full):
        a, b = m.span()
        lit = m.group(0)
        # 节点片段里的比例：是该节点的比例 / 分次，或等于代码算出的累计值（表格累计列，顺带交叉核对）
        in_stage = any(
            s0 <= a and b <= s1 and (lit in lits or abs((parse_pct(lit) or -1) - end) <= _TOL)
            for (s0, s1), lits, end in claims
        )
        in_excluded = any(e0 <= a and b <= e1 for e0, e1 in excluded)
        if not (in_stage or in_excluded):
            return Result([], f"原文中的「{lit}」没有着落", raw)
    return Result(stages, "", raw)


async def parse_with_llm(text: str, cache_dir: Path, *, offline: bool = False) -> Result:
    from app.services import llm_client

    prompt = PROMPT.replace("{text}", text)
    key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
    path = cache_dir / f"{key}.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif offline:
        return Result([], "缓存未命中（离线）", None)
    else:
        raw = await llm_client.chat([{"role": "user", "content": prompt}], json_mode=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    return validate(text, raw)
