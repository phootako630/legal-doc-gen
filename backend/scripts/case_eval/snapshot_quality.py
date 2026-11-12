# 线上案件快照 → 抽取质量信号（#74 第 2 步）。快照的写法见 app/services/case_snapshot.py。
#
# 律师导出过起诉状的案件，比对「交给律师审核时的字段」与「律师最终确认的字段」，回答：
#   - 哪些字段最常被律师改（补填 / 清空 / 修改）
#   - 静默错误：审核页标「正常」、律师却改了的字段（比缺失危险得多）
#   - 审核页状态、来源通道、confidence 分段与改动率的对照（confidence 权重是凭经验定的，用数据检验）
#   - 断点处律师选了第几个候选还是自填（几乎总选同一个的断点，可以考虑改成规则少问一次）
#   - 交付时与导出时的起诉状就绪度
# 只输出字段 key、类别与计数，不输出任何取值：报告可以贴到 issue（只放汇总数字）。
#
# 字段状态按审核页口径还原（前端 lib/buildReviewFields.ts）：值为空 → 缺失；出处写着冲突 / 不一致 → 冲突；
# 出处含 OCR / 扫描 / 待核实 → 待核实；其余正常；确定性校验判为冲突的字段一律冲突（优先级最高）。
# 校验结论来自快照的 delivered_review（#162 起才记）；更早的快照还原不出冲突，不计入静默错误，单独计数。
# 只统计审核页实际展示的字段（REVIEW_FIELD_KEYS，与前端 reviewFieldKeys 同步）：律师导出时提交的是完整字段，
# 里面还有审核页不显示的中间字段（三源台数、联网状态等），算进来会稀释改动率（#163 评审）。
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app import config
from app.services import case_snapshot
from app.services.complaint_readiness import conflict_keys, readiness
from app.services.validators import run_all_checks
from scripts.case_eval.scoring import REVIEW_FIELD_KEYS, _contacts_text, _norm_text

STATUS_LABELS = {"normal": "正常", "ocr_uncertain": "待核实", "missing": "缺失", "conflict": "冲突"}
CHANNEL_LABELS = {"text": "文字", "ocr": "OCR"}
CONF_BUCKETS = ("0–59", "60–79", "80–100")
EDIT_KINDS = ("补填", "清空", "修改")
CUSTOM = "自填"
# 断点「几乎总选同一个」：至少这么多次作答、同一选择占比不低于这个比例
SAME_CHOICE_MIN, SAME_CHOICE_SHARE = 3, 0.9


@dataclass(frozen=True)
class FieldObs:
    """一个案件里一个字段：交付时的状态 / 通道 / confidence，与律师的改动（None 为没改）。"""

    run_id: str
    key: str
    status: str | None  # None：旧快照没有校验结论，还原不出审核页状态
    channel: str | None
    confidence: int | None
    delivered_blank: bool
    edit: str | None


def load_cases(snapshot_dir: str) -> list[dict[str, Any]]:
    """律师导出过起诉状的案件快照，按开始时间排序。"""
    config.SNAPSHOT_DIR = snapshot_dir
    folder = os.path.join(snapshot_dir, "cases")
    cases = []
    for run_id in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        case = case_snapshot.load(run_id)
        if case and case.get("final") and isinstance(case.get("delivered"), dict):
            cases.append(case)
    return sorted(cases, key=lambda c: c.get("started_at") or 0)


def _value(node: Any) -> Any:
    if isinstance(node, list):  # 联系人：按审核页的汇总写法
        return _contacts_text(node)
    return node.get("value") if isinstance(node, dict) else node


def _blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _same(a: Any, b: Any) -> bool:
    """全角半角、空白、数字写法（1000 / 1000.0 / 1,000）不算差异。"""
    x, y = _norm_text(a), _norm_text(b)
    if x == y:
        return True
    try:
        return float(x.replace(",", "")) == float(y.replace(",", ""))
    except ValueError:
        return False


def edit_kind(before: Any, after: Any) -> str | None:
    """律师相对交付值的改动：补填 / 清空 / 修改；没改为 None。"""
    if _blank(before) and _blank(after):
        return None
    if _blank(before):
        return "补填"
    if _blank(after):
        return "清空"
    return None if _same(before, after) else "修改"


def review_status(node: Any, conflicts: set[str], key: str) -> str:
    """审核页上这个字段的状态（同前端 buildReviewFields / inferStatus）。"""
    if key in conflicts:
        return "conflict"
    if isinstance(node, list):
        return "normal" if _contacts_text(node) else "missing"
    node = node if isinstance(node, dict) else {}
    if node.get("value") is None:
        return "missing"
    src = str(node.get("src") or "").lower()
    if "冲突" in src or "不一致" in src:
        return "conflict"
    if "ocr" in src or "扫描" in src or "待核实" in src:
        return "ocr_uncertain"
    return "normal"


def field_observations(case: dict[str, Any]) -> list[FieldObs]:
    delivered, final = case["delivered"], case["final"]
    review = case.get("delivered_review")
    conflicts = conflict_keys(review.get("validations") or []) if isinstance(review, dict) else None
    out = []
    for key in REVIEW_FIELD_KEYS:
        if key not in delivered or key not in final:
            continue
        node, after = delivered[key], final[key]
        if not (isinstance(node, list) or (isinstance(node, dict) and "value" in node)):
            continue
        before = _value(node)
        meta = node if isinstance(node, dict) else {}
        conf = meta.get("confidence")
        out.append(FieldObs(
            run_id=case.get("run_id") or "",
            key=key,
            status=review_status(node, conflicts, key) if conflicts is not None else None,
            channel=meta.get("channel"),
            confidence=conf if isinstance(conf, int) and not isinstance(conf, bool) else None,
            delivered_blank=_blank(before),
            edit=edit_kind(before, _value(after)),
        ))
    return out


def _options(pending: dict[str, Any], key: str) -> list[Any]:
    per_field = (pending.get("field_options") or {}).get(key)
    if per_field:
        return [o.get("value") if isinstance(o, dict) else o for o in per_field]
    return list(pending.get("options") or [])


def decision_choices(case: dict[str, Any]) -> tuple[list[tuple[str, str, str]], int]:
    """每轮断点里每个字段律师选了第几个候选（「候选 N」）还是自填；返回 (列表, 没记候选项的旧轮次数)。"""
    choices, old_rounds = [], 0
    for rnd in case.get("decisions") or []:
        pending = rnd.get("pending")
        if not isinstance(pending, dict):
            old_rounds += 1
            continue
        kind = str(pending.get("kind") or "未知")
        for key, answer in (rnd.get("answers") or {}).items():
            options = _options(pending, key)
            hit = next((i for i, opt in enumerate(options, 1) if not _blank(opt) and _same(opt, answer)), None)
            choices.append((kind, key, f"候选 {hit}" if hit else CUSTOM))
    return choices, old_rounds


def _conf_bucket(conf: int | None) -> str:
    if conf is None:
        return "无"
    return CONF_BUCKETS[0] if conf < 60 else CONF_BUCKETS[1] if conf < 80 else CONF_BUCKETS[2]


def final_readiness(final: dict[str, Any]) -> int | None:
    """
    律师导出时的就绪度：对律师导出的字段重新跑确定性校验，再用 agent 同一个就绪度函数
    （services/complaint_readiness.readiness）——停在断点直接导出、没解决的冲突照样不算就绪（#163 评审）。
    """
    try:
        return readiness(final, run_all_checks(final))
    except Exception:  # noqa: BLE001 — 旧快照字段不全时不让整份报告失败
        return None


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    obs = [o for c in cases for o in field_observations(c)]
    per_key: dict[str, Counter] = defaultdict(Counter)
    for o in obs:
        per_key[o.key]["cases"] += 1
        if o.edit:
            per_key[o.key][o.edit] += 1
    # 有交付值的字段：律师改（修改 / 清空）的比例，分别按状态、通道、confidence 分段
    by_status: dict[str, Counter] = defaultdict(Counter)
    by_channel: dict[str, Counter] = defaultdict(Counter)
    by_conf: dict[str, Counter] = defaultdict(Counter)
    silent: Counter = Counter()
    for o in obs:
        if o.status is not None:
            by_status[o.status]["n"] += 1
            by_status[o.status]["edited"] += bool(o.edit)
            if o.status == "normal" and o.edit in ("修改", "清空"):
                silent[o.key] += 1
        if o.delivered_blank:
            continue
        changed = o.edit in ("修改", "清空")
        for table, label in ((by_channel, CHANNEL_LABELS.get(o.channel or "", "未知")),
                             (by_conf, _conf_bucket(o.confidence))):
            table[label]["n"] += 1
            table[label]["edited"] += changed
    choices: dict[tuple[str, str], Counter] = defaultdict(Counter)
    old_rounds = 0
    for c in cases:
        picked, old = decision_choices(c)
        old_rounds += old
        for kind, key, choice in picked:
            choices[(kind, key)][choice] += 1
    delivered_ready = [r for c in cases
                       if isinstance(r := (c.get("delivered_review") or {}).get("readiness"), int)]
    final_ready = [r for c in cases if (r := final_readiness(c["final"])) is not None]
    edits_per_case = [sum(1 for o in field_observations(c) if o.edit) for c in cases]
    return {
        "cases": len(cases),
        "with_review": sum(1 for c in cases if isinstance(c.get("delivered_review"), dict)),
        "edits_per_case": edits_per_case,
        "per_key": per_key,
        "silent": silent,
        "by_status": by_status,
        "by_channel": by_channel,
        "by_conf": by_conf,
        "choices": choices,
        "old_rounds": old_rounds,
        "delivered_ready": delivered_ready,
        "final_ready": final_ready,
    }


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def _rate_table(title: str, table: dict[str, Counter], order: list[str]) -> list[str]:
    lines = [f"| {title} | 字段数 | 律师改了 | 改动率 |", "|---|---|---|---|"]
    for label in order + sorted(k for k in table if k not in order):
        if label in table:
            n, e = table[label]["n"], table[label]["edited"]
            lines.append(f"| {label} | {n} | {e} | {_pct(e, n)} |")
    return lines


def _ready_line(name: str, values: list[int]) -> str:
    if not values:
        return f"- {name}：无数据"
    full = sum(1 for v in values if v == 100)
    return (f"- {name}：平均 {sum(values) / len(values):.0f}%，最低 {min(values)}%，"
            f"100% 的 {full}/{len(values)} 个案件")


def render(s: dict[str, Any]) -> str:
    """中文 Markdown 报告：只有字段 key、类别与计数。"""
    n = s["cases"]
    lines = ["# 抽取质量信号（线上案件快照）", ""]
    if not n:
        return "\n".join(lines + ["还没有律师导出过起诉状的案件。", ""])
    edits = s["edits_per_case"]
    lines += [
        f"- 案件：{n} 个（律师导出过起诉状）；其中 {s['with_review']} 个记了交付时的校验结论，能还原审核页状态",
        f"- 每个案件律师改的字段：平均 {sum(edits) / n:.1f} 个，最多 {max(edits)} 个，"
        f"一个都没改的 {sum(1 for e in edits if not e)} 个",
        "",
        "## 律师改得最多的字段",
        "",
        "| 字段 | 案件数 | 补填 | 清空 | 修改 | 改动率 |",
        "|---|---|---|---|---|---|",
    ]
    ranked = sorted(((k, c) for k, c in s["per_key"].items() if any(c[e] for e in EDIT_KINDS)),
                    key=lambda kc: (-sum(kc[1][e] for e in EDIT_KINDS) / kc[1]["cases"], kc[0]))
    for key, c in ranked:
        total = sum(c[e] for e in EDIT_KINDS)
        lines.append(f"| `{key}` | {c['cases']} | {c['补填']} | {c['清空']} | {c['修改']} | {_pct(total, c['cases'])} |")
    if not ranked:
        lines.append("| （律师没改过任何字段） | | | | | |")

    lines += ["", "## 静默错误：审核页标「正常」、律师却改了（修改 / 清空）", ""]
    if s["silent"]:
        lines += ["| 字段 | 案件数 |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(s["silent"].items(), key=lambda kv: (-kv[1], kv[0]))]
    else:
        lines.append("没有。")
    if s["with_review"] < n:
        lines.append(f"\n另有 {n - s['with_review']} 个旧快照没记校验结论，还原不出审核页状态，未计入本节和下一节。")

    lines += ["", "## 审核页状态与改动率", ""]
    lines += _rate_table("状态", {STATUS_LABELS.get(k, k): v for k, v in s["by_status"].items()},
                         list(STATUS_LABELS.values()))
    lines += ["", "以下两节只算交付时有值的字段（律师「修改 / 清空」才算改）。", "", "## 来源通道与改动率", ""]
    lines += _rate_table("通道", s["by_channel"], list(CHANNEL_LABELS.values()))
    lines += ["", "## confidence 分段与改动率", ""]
    lines += _rate_table("confidence", s["by_conf"], list(CONF_BUCKETS))
    lines.append("\n分数越高、改动率应越低；不是这样，说明 confidence 的权重需要调整。")

    lines += ["", "## 断点：律师选了第几个候选", ""]
    if s["choices"]:
        lines += ["| 断点类型 | 字段 | 作答次数 | 选择分布 | 提示 |", "|---|---|---|---|---|"]
        for (kind, key), c in sorted(s["choices"].items()):
            total = sum(c.values())
            dist = "、".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))
            top = max(c.values())
            hint = "几乎总选同一个，可考虑改成规则" if total >= SAME_CHOICE_MIN and top / total >= SAME_CHOICE_SHARE else ""
            lines.append(f"| {kind} | `{key}` | {total} | {dist} | {hint} |")
    else:
        lines.append("没有记了候选项的断点作答。")
    if s["old_rounds"]:
        lines.append(f"\n另有 {s['old_rounds']} 轮作答来自旧快照，没记候选项，未计入。")

    lines += ["", "## 起诉状就绪度", "", _ready_line("交给律师时", s["delivered_ready"]),
              _ready_line("律师导出时", s["final_ready"]),
              "", "导出时的就绪度对律师导出的字段重新校验：没解决的冲突同样不算就绪。", ""]
    return "\n".join(lines)
