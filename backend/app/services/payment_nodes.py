# 付款条款拆成付款节点并算累计比例；用「已付 + 欠款」占合同总价的比例匹配节点（确认单（五）第 3 题）。
#
# 律师答复：起诉状只引用欠款对应的付款节点，条款号写到该项，照抄原文。例：付款节点 定金 5% →
# 排产 25% → 提货 55% → 验收移交付至 95% → 质保金 5%，已付 92.4%、已付 + 欠款 = 100%，
# 欠的是验收款的一部分和质保金，引用这两项。律师反馈：「已付 + 欠款」占比与任何节点的累计比例
# 都对不上（如算出 85%，节点为 5%-10%-70%-95%-100%）时，可能调过价、实际台数与合同不同、
# 质保金到期时间不统一，交律师手动核对，不猜。
#
# 付款条款写成表格时按行拆（`_table_stages`）；写成「第一期：……。第二期：……」的按分期标记拆。
# 纯确定性解析：比例只认「支付 / 付……X%」（本期）与「支付至 / 付至……X%」（累计）两种写法；
# 「开具……5%……保函」、违约金 / 利息这类不是付款的百分比不计。解析不出、累计倒退、超过或到不了 100% 时
# 返回空，退回原来的写法。
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.equipment_list import table_rows

_TOL = 0.05  # 百分点：金额四舍五入到分后，比例会有微小误差

# 节点编号：多级编号（7.3、4.2.1、3.11）后面不能紧跟数字或 %；一级编号须带「、」或「.」（「2. 验收款」）。
# 编号前须是开头、空白、句末标点或省略号，避免把「；25个工作日内」当成编号
_NODE_NO_RE = re.compile(
    r"(?:^|(?<=[\s；;。：:…]))"
    r"(?:(?P<multi>\d{1,2}(?:[.．]\d{1,2}){1,3})(?![\d%.．])[、.．]?|(?P<single>\d{1,2})[、.．](?!\d))"
)
_PCT = r"(\d+(?:\.\d+)?)\s*[%％]"
# 累计：「支付至 / 付至 / 支付到……X%」；「货款付到85%时」是抵扣条件，不是付款节点
_CUMULATIVE_RE = re.compile(rf"(?:支付至|付至|支付到|累计支付|累计付)[^%％，,；;。]{{0,30}}?{_PCT}(?!\s*时)")
# 本期：「支付……X%」
_STAGE_RE = re.compile(rf"(?:支付|付款|付)(?!至|到)[^%％，,；;。]{{0,30}}?{_PCT}")
# 余款 / 质保金写法：「余款5%在质保期满后……支付」「质保金为合同总价的5%」
_REMAINDER_RE = re.compile(rf"(?:余款|尾款|剩余款|质保金|保修金|保修款)[^%％，,；;。]{{0,12}}?{_PCT}")


# 「第一期：」「第2期，」这类分期标记
_PERIOD_RE = re.compile(r"第[一二三四五六七八九十\d]{1,3}期[：:，,]")


@dataclass
class PaymentStage:
    no: str  # 条款编号（原文写法，可能为空）
    text: str  # 该节点原文（去掉编号）
    start: float  # 本节点开始时已累计的比例（%）
    end: float  # 本节点付完后累计的比例（%）


def _segments(text: str) -> list[tuple[str, str]]:
    """按条款编号切分，返回 [(编号, 原文)]；没有编号时整段一个节点。"""
    marks = list(_NODE_NO_RE.finditer(text))
    if not marks:
        return [("", text.strip())]
    out = []
    head = text[: marks[0].start()].strip()
    if head:
        out.append(("", head))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group("multi") or m.group("single"), text[m.end() : end].strip()))
    return out


# 百分比后紧跟「（的）……保函」：比例说的是保函金额
_GUARANTEE_PCT_RE = re.compile(r"(?:的)?[^，,；;。在后]{0,10}?保函")
# 违约金 / 利息的百分比（「逾期支付的，按欠款的0.05%支付违约金」）不是付款节点
_PENALTY_RE = re.compile(r"违约|利息|滞纳|罚")


def _ratio(piece: str) -> tuple[str, float] | None:
    """一个分句的付款比例：('cum', X) 累计 / ('stage', X) 本期；没有付款比例返回 None。"""
    if _PENALTY_RE.search(piece):
        return None
    m = _CUMULATIVE_RE.search(piece)
    if m:
        return "cum", float(m.group(1))
    m = _STAGE_RE.search(piece)
    if not m:
        m = _REMAINDER_RE.search(piece)
        # 「质保金：乙方向甲方开具合同总价5%的银行质保保函」：百分比修饰的是保函，不是支付价款；
        # 「余款5%在甲方收到银行质保保函后支付」里保函只是付款前提，照常计入（#54 评审）
        if m and _GUARANTEE_PCT_RE.match(piece, m.end()):
            m = None
    if m:
        return "stage", float(m.group(1))
    return None


_PCT_CELL_RE = re.compile(_PCT)
_TOTAL_ROW_RE = re.compile(r"合\s*计|总\s*计|小\s*计|共\s*计")
_NO_CELL_RE = re.compile(r"^(?:第)?\s*(\d{1,2}(?:[.．]\d{1,2}){0,3})\s*(?:[期条项、.．])?$")


def _table_stages(text: str) -> list[PaymentStage]:
    """
    付款条款写成表格（OCR 出来是 Markdown / HTML 表格，律师反馈）：每行一个付款节点。
    表头含「累计」的列按累计比例，含「比例 / 百分比」的列按本期比例；没有表头时取行内第一个百分比。
    序号列（「1」「第2期」「4.2.1」）作条款编号，其余单元格照抄原文作节点内容。
    """
    rows = table_rows(text)
    if len(rows) < 2:
        return []
    cum_col = stage_col = None
    stages: list[PaymentStage] = []
    running = 0.0
    for cells in rows:
        if not any(_PCT_CELL_RE.search(c) for c in cells):
            # 表头：记下累计 / 本期比例所在列
            for i, c in enumerate(cells):
                if "累计" in c:
                    cum_col = i
                elif re.search(r"比例|百分比|%|％", c) and stage_col is None:
                    stage_col = i
            continue
        row = "".join(cells)
        # 违约金 / 利息行、「合计 / 总计 100%」汇总行不是付款节点（#57 评审）
        if _PENALTY_RE.search(row) or _TOTAL_ROW_RE.search(row):
            continue
        # 「银行质保保函｜5%｜乙方向甲方开具保函」是开保函，不是支付价款；同一行写了支付（「收到保函后支付」）才算
        if "保函" in row and not re.search(r"支付|付款|付清", row):
            continue
        no_match = _NO_CELL_RE.match(cells[0].strip()) if cells else None
        no = no_match.group(1) if no_match else ""

        def pct(col: int | None) -> float | None:
            if col is None or col >= len(cells):
                return None
            m = _PCT_CELL_RE.search(cells[col])
            return float(m.group(1)) if m else None

        cum = pct(cum_col)
        if cum is None:
            stage = pct(stage_col)
            if stage is None:
                first = next((m for c in cells if (m := _PCT_CELL_RE.search(c))), None)
                stage = float(first.group(1)) if first else None
            if stage is None:
                continue
            cum = running + stage
        if cum < running - _TOL or cum > 100 + _TOL:
            return []
        body = "，".join(c.strip() for c in (cells[1:] if no else cells) if c.strip())
        stages.append(PaymentStage(no, body, running, cum))
        running = cum
    return stages if stages and abs(running - 100) <= _TOL else []


def parse_payment_stages(text: str | None) -> list[PaymentStage]:
    """付款条款原文 → 按顺序的付款节点（含累计区间）。解析不出或累计不等于 100% 时返回空列表。"""
    if not text or not text.strip():
        return []
    table = _table_stages(text)
    if table:
        return table
    stages: list[PaymentStage] = []
    running = 0.0
    for no, body in _segments(text):
        # 同一项里用分号隔开的多期付款（「进场后支付20%；安装完成后支付60%」）各算一个节点；
        # 写成「第一期：……。第二期：……」的按「第X期」切（#127：各期之间是句号，不是分号）
        splitter = r"[；;]|(?=第[一二三四五六七八九十\d]{1,3}期[：:，,])" if len(_PERIOD_RE.findall(body)) >= 2 else r"[；;]"
        pieces = [p for p in re.split(splitter, body) if p.strip()]
        for piece in pieces:
            r = _ratio(piece)
            if r is None:
                continue
            kind, value = r
            end = value if kind == "cum" else running + value
            if end < running - _TOL or end > 100 + _TOL:
                return []  # 累计倒退或超过 100%：多半没读对，不用
            text_out = piece.strip() if len(pieces) > 1 else body.strip()
            stages.append(PaymentStage(no, text_out.rstrip("；;，, "), running, end))
            running = end
    # 累计到不了 100%：多半只抽到了部分条款（如只有最后一期），不拿来匹配
    if abs(running - 100) > _TOL:
        return []
    return stages


def match_cumulative(stages: list[PaymentStage], ratio: float) -> PaymentStage | None:
    """累计比例等于 ratio（%）的节点；对不上返回 None。"""
    for s in stages:
        if abs(s.end - ratio) <= _TOL:
            return s
    return None


def owed_stages(stages: list[PaymentStage], paid_ratio: float, due_ratio: float) -> list[PaymentStage]:
    """欠款（已付比例到「已付 + 欠款」比例之间）落在哪些节点。"""
    return [s for s in stages if s.end > paid_ratio + _TOL and s.start < due_ratio - _TOL]
