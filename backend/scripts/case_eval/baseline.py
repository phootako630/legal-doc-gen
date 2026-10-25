# eval 基线与回归门槛（#82）：把一次运行的逐字段结论存成 baseline.json，之后的运行与之比较。
#
# 变差（任一即失败）：正确 → 错误 / 缺失、缺失 → 错误（「错误」是看起来正常的错值，比【待补充】危险）、
# 来源由对变错、新出现静默错误、案件运行出错（含严格回放缓存未命中）、基线里没有的案件。
# 变好不算失败，只提示用 --update-baseline 更新基线。待定字段不比较。
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from scripts.case_eval.scoring import CORRECT, MISSING, PENDING, WRONG

# 越大越好：错误（静默的错值）比缺失（文书里显示【待补充】）更糟
_RANK = {WRONG: 0, MISSING: 1, CORRECT: 2}


def snapshot(cases: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """一次运行的逐字段结论（只存结论，不存取值）。出错的案件不写入。"""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for c in cases:
        if c.get("error"):
            continue
        out[c["case"]] = {
            r["key"]: {"verdict": r["verdict"], "source_ok": r.get("source_ok"), "silent": bool(r.get("silent"))}
            for r in c.get("results") or []
        }
    return out


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
        # OCR 未命中发生在线程里、被当成单页失败跳过，案件不一定出错：看调用统计
        missed = (c.get("stats") or {}).get("misses") or "CacheMiss" in (c.get("error") or "")
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


def render(gate: GateResult, baseline_path: str) -> str:
    lines = ["## 基线门槛", ""]
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
