# LLM 裁判校准（#90 第 3 步）：拿人工结论当标准答案，看裁判跟人对上多少，决定裁判结论能不能计分。
#
# 数据：一组「律师句 / 系统句」配对（pairs.json，真实案卷的配对只放服务器 / 会话临时目录，不进仓库）：
#   [{"id": "c1-jurisdiction", "case": "案件名", "key": "jurisdiction_text", "lawyer": "…", "system": "…",
#     "origin": "真实" 或 "构造", "criteria": "选填，覆盖默认标准"}]
#   「构造」= 在真实句子上改一处（法院、比例、条款号…）造的反例，测裁判抓不抓得到实质差异。
# 人工结论：标注表（Excel）里逐对填「一致 / 不一致 / 无法判断」；表里不显示裁判结论，免得先入为主。
#
# 指标：
#   - 一致率：裁判与人工结论相同的比例；
#   - Cohen's kappa：扣掉「碰巧对上」后的一致程度（大部分配对本来就一致时，一致率会虚高）；
#   - 混淆矩阵：人工 × 裁判，重点看「人工不一致、裁判一致」——裁判放过了实质差异，最危险；
#   - 分「全部」与「只看模型判的」两组：代码直接判的配对（逐字相同 / 一边为空）不反映模型水平。
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from scripts.case_eval.judge import BY_CODE, NOT_JUDGED, VERDICTS, Ask, judge_one
from scripts.case_eval.judge_rubrics import judge_allowed, judge_criteria

SHEET_HEADERS = ("编号", "字段", "判断标准", "律师句", "系统句", "你的结论", "备注")
LABEL_COL = SHEET_HEADERS.index("你的结论")


@dataclass
class Pair:
    id: str
    case: str
    key: str
    lawyer: Any
    system: Any
    origin: str = "真实"
    criteria: str = ""
    human: str = ""  # 人工结论
    verdict: str = ""  # 裁判结论
    reason: str = ""
    by: str = ""


def load_pairs(path: str) -> list[Pair]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    pairs, seen = [], set()
    for item in raw:
        key = item["key"]
        if not judge_allowed(key):
            raise ValueError(f"{item['id']}：{key} 不在裁判白名单里，不能拿来校准")
        if item["id"] in seen:
            raise ValueError(f"编号重复：{item['id']}")
        seen.add(item["id"])
        pairs.append(Pair(
            id=item["id"], case=item.get("case", ""), key=key, lawyer=item.get("lawyer"),
            system=item.get("system"), origin=item.get("origin", "真实"),
            criteria=judge_criteria(key, item.get("criteria")),
        ))
    return pairs


def write_sheet(pairs: list[Pair], path: str, seed: int | None = None) -> None:
    """
    标注表：每对一行，「你的结论」一栏下拉选择；不含裁判结论，也不标真实 / 构造。
    行序打乱（#154 评审：构造反例若集中排列，标注的人会从行序猜出分组），人工结论按编号对回；
    seed 固定时顺序可复现。编号本身也不要透露真实 / 构造（配对文件里用 P01 这类中性编号）。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "标注"
    ws.append(SHEET_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    rows = list(pairs)
    random.Random(seed).shuffle(rows)
    for p in rows:
        ws.append([p.id, p.key, p.criteria, _text(p.lawyer), _text(p.system), "", ""])
    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    col = chr(ord("A") + LABEL_COL)
    dv.add(f"{col}2:{col}{len(pairs) + 1}")
    for letter, width in zip("ABCDEFG", (16, 22, 40, 60, 60, 12, 30)):
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def read_labels(path: str) -> dict[str, str]:
    """标注表里填了的人工结论 {编号: 结论}；没填的跳过，填了不认识的值报错。"""
    from openpyxl import load_workbook

    ws = load_workbook(path, read_only=True).active
    labels = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        label = str(row[LABEL_COL] or "").strip()
        if not label:
            continue
        if label not in VERDICTS:
            raise ValueError(f"{row[0]}：结论只能填 {'/'.join(VERDICTS)}，表里是「{label}」")
        labels[str(row[0])] = label
    return labels


async def judge_pairs(pairs: list[Pair], ask: Ask, model: str) -> None:
    for p in pairs:
        p.verdict, p.reason, p.by = await judge_one(p.criteria, p.lawyer, p.system, ask, model)


def confusion(pairs: list[Pair]) -> dict[str, dict[str, int]]:
    """人工结论 × 裁判结论的计数（只算人工已标、裁判有结论的配对）。"""
    m = {h: {j: 0 for j in VERDICTS} for h in VERDICTS}
    for p in _scored(pairs):
        m[p.human][p.verdict] += 1
    return m


def agreement(pairs: list[Pair]) -> float | None:
    done = _scored(pairs)
    return sum(p.human == p.verdict for p in done) / len(done) if done else None


def cohen_kappa(pairs: list[Pair]) -> float | None:
    """
    κ = (实际一致率 − 碰巧一致率) / (1 − 碰巧一致率)；碰巧一致率按两边各自的结论分布算。
    两边都只用了同一种结论时分母为 0、κ 没有定义，返回 None（报告显示「—」），
    不当成完美校准（#154 评审：这正是要暴露的类别失衡）。
    """
    done = _scored(pairs)
    if not done:
        return None
    n = len(done)
    po = sum(p.human == p.verdict for p in done) / n
    pe = sum(
        (sum(p.human == v for p in done) / n) * (sum(p.verdict == v for p in done) / n) for v in VERDICTS
    )
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def _scored(pairs: list[Pair]) -> list[Pair]:
    return [p for p in pairs if p.human and p.verdict in VERDICTS]


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def _cell(v: Any) -> str:
    return _text(v).replace("|", "｜").replace("\n", " ") or "（空）"


def _fmt(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:.0%}" if pct else f"{x:.2f}"


def _matrix(pairs: list[Pair]) -> list[str]:
    m = confusion(pairs)
    lines = ["| 人工 ＼ 裁判 | " + " | ".join(VERDICTS) + " |", "|---|" + "---|" * len(VERDICTS)]
    lines += [f"| {h} | " + " | ".join(str(m[h][j]) for j in VERDICTS) + " |" for h in VERDICTS]
    return lines


def render(pairs: list[Pair], model: str) -> str:
    model_pairs = [p for p in pairs if p.by not in (BY_CODE, "")]
    labeled = [p for p in pairs if p.human]
    missed = [p for p in pairs if p.verdict == NOT_JUDGED]
    out = [f"# LLM 裁判校准报告（裁判：{model}）", ""]
    out.append(f"共 {len(pairs)} 对，人工已标 {len(labeled)} 对；代码直接判 "
               f"{sum(p.by == BY_CODE for p in pairs)} 对，模型判 {len(model_pairs)} 对"
               + (f"，未裁判 {len(missed)} 对（不计入下面的指标）" if missed else "") + "。")
    out += ["", "| 范围 | 对数 | 一致率 | Cohen's kappa |", "|---|---|---|---|"]
    for name, group in (("全部", pairs), ("只看模型判的", model_pairs),
                        ("真实配对", [p for p in pairs if p.origin == "真实"]),
                        ("构造反例", [p for p in pairs if p.origin == "构造"])):
        out.append(f"| {name} | {len(_scored(group))} | {_fmt(agreement(group), True)} | "
                   f"{_fmt(cohen_kappa(group))} |")
    out += ["", "## 混淆矩阵（只看模型判的）", ""] + _matrix(model_pairs)
    danger = [p for p in _scored(pairs) if p.human == "不一致" and p.verdict == "一致"]
    out += ["", f"「人工不一致、裁判一致」：{len(danger)} 对——裁判放过了实质差异，最危险。", ""]
    keys = sorted({p.key for p in pairs})
    out += ["## 按字段", "", "| 字段 | 对数 | 一致率 |", "|---|---|---|"]
    out += [f"| `{k}` | {len(_scored([p for p in pairs if p.key == k]))} | "
            f"{_fmt(agreement([p for p in pairs if p.key == k]), True)} |" for k in keys]
    diff = [p for p in _scored(pairs) if p.human != p.verdict]
    out += ["", "## 裁判与人工不同的配对", ""]
    if diff:
        out += ["| 编号 | 字段 | 来源 | 人工 | 裁判 | 裁判理由 | 律师句 | 系统句 |", "|---|---|---|---|---|---|---|---|"]
        out += [f"| {p.id} | `{p.key}` | {p.origin} | {p.human} | {p.verdict} | {_cell(p.reason)} | "
                f"{_cell(p.lawyer)} | {_cell(p.system)} |" for p in diff]
    else:
        out.append("无。")
    out.append("")
    return "\n".join(out)
