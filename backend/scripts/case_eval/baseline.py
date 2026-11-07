# eval 基线与回归门槛（#82）：把一次运行的逐字段结论存成 baseline.json，之后的运行与之比较。
#
# 变差（任一即失败）：正确 → 错误 / 缺失、缺失 → 错误（「错误」是看起来正常的错值，比【待补充】危险）、
# 来源由对变错、新出现静默错误、案件运行出错（含严格回放缓存未命中）、基线里没有的案件。
# 变好不算失败，只提示用 --update-baseline 更新基线。待定字段不比较。
#
# LLM 裁判（#90 第 4 步）：judge 字段的裁判结论也存进基线，变化只提示、不算失败（裁判校准前不计分）；
# 裁判缓存未命中同样只提示，不让 CI 失败。
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from scripts.case_eval.scoring import CORRECT, MISSING, PENDING, WRONG

JUDGE_MISS = "裁判"  # ReplayCache 记录的裁判缓存未命中（见 cache.wrap_judge）

# 越大越好：错误（静默的错值）比缺失（文书里显示【待补充】）更糟
_RANK = {WRONG: 0, MISSING: 1, CORRECT: 2}


def snapshot(cases: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """一次运行的逐字段结论（只存结论，不存取值）。出错的案件不写入。"""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for c in cases:
        if c.get("error"):
            continue
        out[c["case"]] = {r["key"]: _snap(r) for r in c.get("results") or []}
    return out


def _snap(r: dict[str, Any]) -> dict[str, Any]:
    snap = {"verdict": r["verdict"], "source_ok": r.get("source_ok"), "silent": bool(r.get("silent"))}
    if r.get("match") == "judge":
        snap["judge"] = r.get("judge_verdict") or ""
    return snap


def load(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path: str, cases: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"cases": snapshot(cases)}, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


@dataclass
class GateResult:
    regressions: list[str] = field(default_factory=list)  # 变差：门槛失败
    improvements: list[str] = field(default_factory=list)  # 变好：提示更新基线
    errors: list[str] = field(default_factory=list)  # 案件运行出错 / 缓存未命中 / 基线缺案件
    warnings: list[str] = field(default_factory=list)  # 裁判结论变化、裁判缓存未命中：只提示，不算失败

    @property
    def failed(self) -> bool:
        return bool(self.regressions or self.errors)


def _desc(r: dict[str, Any]) -> str:
    parts = [r["verdict"]]
    if r.get("source_ok") is False:
        parts.append("来源错误")
    if r.get("silent"):
        parts.append("静默错误")
    return "，".join(parts)


def compare(baseline: dict[str, Any], cases: list[dict[str, Any]], root: str) -> GateResult:
    gate = GateResult()
    base = baseline.get("cases") or {}
    for c in cases:
        name = c["case"]
        # OCR 未命中发生在线程里、被当成单页失败跳过，案件不一定出错：看调用统计。
        # 裁判未命中不影响打分（judge 字段记「未裁判」），只提示
        misses = (c.get("stats") or {}).get("misses") or []
        if JUDGE_MISS in misses:
            gate.warnings.append(
                f"{name}：LLM 裁判缓存未命中（裁判的提示词或系统句变了），相关字段记为「未裁判」。"
                f"需要时在本地重录（需要 DASHSCOPE_API_KEY）：python scripts/eval_cases.py {root} --case {name}"
            )
        missed = [m for m in misses if m != JUDGE_MISS] or "CacheMiss" in (c.get("error") or "")
        if missed or c.get("error"):
            if missed:
                gate.errors.append(
                    f"{name}：严格回放缓存未命中（这次改动让系统读了新的页或发了新的提示词）。"
                    f"在本地重录（需要 API Key）：rm -rf {os.path.join(root, name, 'cache')} && "
                    f"python scripts/eval_cases.py {root} --case {name} --mode live，"
                    "核对结论后提交新缓存，并在 PR 里说明原因"
                )
            else:
                gate.errors.append(f"{name}：运行出错 {c['error']}")
            continue
        if name not in base:
            gate.errors.append(f"{name}：基线里没有这个案件，确认结论无误后用 --update-baseline 写入")
            continue
        old_fields = base[name]
        gate.warnings += _judge_warnings(name, old_fields, c.get("results") or [])
        for r in c.get("results") or []:
            old = old_fields.get(r["key"])
            if old is None or r["verdict"] == PENDING or old["verdict"] == PENDING:
                continue
            new_rank, old_rank = _RANK.get(r["verdict"], 0), _RANK.get(old["verdict"], 0)
            worse = (
                new_rank < old_rank
                or (old.get("source_ok") is True and r.get("source_ok") is False)
                or (not old.get("silent") and r.get("silent"))
            )
            better = (
                new_rank > old_rank
                or (old.get("source_ok") is False and r.get("source_ok") is True)
                or (old.get("silent") and not r.get("silent"))
            )
            line = f"{name} / {r['key']}：{_desc(old)} → {_desc(r)}（标准值 {r['expected']}，系统值 {r['actual']}）"
            if worse:
                gate.regressions.append(line)
            elif better:
                gate.improvements.append(line)
    return gate


def _judge_warnings(name: str, old_fields: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    """
    以基线为准逐个检查裁判字段：结论变了要提示；基线里交给裁判、这次却没有——字段被删或不再配
    match: judge——也提示「→ 未裁判」（#155 评审：否则裁判回放悄悄消失，CI 毫无提示）。
    """
    now_by_key = {r["key"]: r for r in results}
    out = []
    for key, old in old_fields.items():
        if "judge" not in old:
            continue
        was = old.get("judge") or "未裁判"
        r = now_by_key.get(key)
        if r is None:
            out.append(f"{name} / {key}：裁判结论 {was} → 未裁判（这次没有这个字段，expected.json 里被删了？）")
        elif r.get("match") != "judge":
            out.append(f"{name} / {key}：裁判结论 {was} → 未裁判（这次不再交给裁判，按「{r.get('match')}」比对）")
        else:
            now = r.get("judge_verdict") or "未裁判"
            if was != now:
                out.append(f"{name} / {key}：裁判结论 {was} → {now}（{r.get('judge_reason') or ''}）")
    # 新配成 judge、基线里还没有裁判结论的字段：提示更新基线
    out += [f"{name} / {r['key']}：新交给裁判的字段（{r.get('judge_verdict') or '未裁判'}），确认后用 --update-baseline 写入基线"
            for r in results if r.get("match") == "judge" and "judge" not in (old_fields.get(r["key"]) or {})]
    return out


def render(gate: GateResult, baseline_path: str) -> str:
    lines = ["## 基线门槛", ""]
    if gate.warnings:
        lines += ["**LLM 裁判提示**（校准前不计分，只提示、不算失败；确认后可用 --update-baseline 更新）：",
                  *[f"- {w}" for w in gate.warnings], ""]
    if not gate.failed and not gate.improvements:
        return "\n".join(lines + ["与基线一致，通过。", ""])
    if gate.errors:
        lines += ["**出错（门槛失败）**：", *[f"- {e}" for e in gate.errors], ""]
    if gate.regressions:
        lines += ["**比基线变差（门槛失败）**：", *[f"- {e}" for e in gate.regressions], ""]
    if gate.improvements:
        lines += [
            f"**比基线变好**（不算失败；确认后运行时加 --update-baseline 更新 {baseline_path}）：",
            *[f"- {e}" for e in gate.improvements],
            "",
        ]
    lines.append("门槛失败。" if gate.failed else "通过（有变好的字段，请更新基线）。")
    lines.append("")
    return "\n".join(lines)
