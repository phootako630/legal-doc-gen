# eval 报告：汇总各案件结论写成 report.md（给人看）与 results.json（给下次对比），
# 并与上一次运行逐字段对比，列出变好 / 变坏的字段（值、来源、静默错误分别看）。
from __future__ import annotations

import json
import os
from typing import Any

from scripts.case_eval.scoring import CORRECT, MISSING, PENDING, SILENT, SOURCE_WRONG, WRONG

_CELL_MAX = 60


def _cell(value: Any) -> str:
    if value is None:
        text = "（空）"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = " ".join(text.split()).replace("|", "｜")
    return text if len(text) <= _CELL_MAX else text[: _CELL_MAX - 1] + "…"


def accuracy(summary: dict[str, int]) -> float | None:
    scored = summary.get(CORRECT, 0) + summary.get(WRONG, 0) + summary.get(MISSING, 0)
    return summary.get(CORRECT, 0) / scored if scored else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def find_previous(runs_dir: str, current: str) -> dict[str, Any] | None:
    """runs_dir 下按目录名（时间戳）排在 current 之前的最近一次 results.json。"""
    if not os.path.isdir(runs_dir):
        return None
    for name in sorted(os.listdir(runs_dir), reverse=True):
        if name >= current:
            continue
        path = os.path.join(runs_dir, name, "results.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["_run"] = name
            return data
    return None


def _label(r: dict[str, Any]) -> str:
    """一个字段的完整结论：值的结论，加上来源错误 / 静默错误（旧版 results.json 没有这两项）。"""
    parts = [r["verdict"]]
    if r.get("source_ok") is False:
        parts.append(SOURCE_WRONG)
    if r.get("silent"):
        parts.append(SILENT)
    return "，".join(parts)


def diff_runs(prev: dict[str, Any] | None, cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    """与上次比，结论变了的字段（同一案件、同一字段；值、来源、静默错误任一变化都算）。"""
    if not prev:
        return []
    before = {
        (c["case"], r["key"]): _label(r)
        for c in prev.get("cases", [])
        for r in c.get("results") or []
    }
    changes = []
    for c in cases:
        for r in c.get("results") or []:
            old = before.get((c["case"], r["key"]))
            if old is not None and old != _label(r):
                changes.append({"case": c["case"], "key": r["key"], "before": old, "after": _label(r)})
    return changes


def _source_cell(q: dict[str, int]) -> str:
    checked = q.get("来源检查", 0)
    return f"{q.get(SOURCE_WRONG, 0)}/{checked}" if checked else "—"


def render_markdown(meta: dict[str, Any], cases: list[dict[str, Any]], changes: list[dict[str, str]],
                    prev_run: str | None) -> str:
    lines = [f"# 案件 eval 报告（{meta['run']}）", ""]
    lines.append(
        f"模式：{meta['mode']}{'（严格回放）' if meta.get('strict') else ''} · "
        f"LLM：{meta['llm_model']} · OCR：{meta['ocr_model']}"
    )
    fallback = sum((c.get("stats") or {}).get("llm_fallback", 0) for c in cases)
    if fallback:
        lines[-1] += f" · DeepSeek 不可用，{fallback} 次调用由备用模型 {meta.get('llm_fallback_model') or ''} 回答"
    lines.append("")
    total = {CORRECT: 0, WRONG: 0, MISSING: 0, PENDING: 0}
    total_q = {SOURCE_WRONG: 0, "来源检查": 0, SILENT: 0}
    lines += [
        "| 案件 | 正确 | 错误 | 缺失 | 待定 | 准确率 | 来源错误 | 静默错误 | 暂停 | 就绪度 "
        "| OCR 页（调用/缓存） | LLM（调用/缓存） | 耗时 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cases:
        s = c.get("summary") or {}
        for k in total:
            total[k] += s.get(k, 0)
        q = c.get("quality") or {}
        for k in total_q:
            total_q[k] += q.get(k, 0)
        st = c.get("stats") or {}
        name = c.get("title") or c["case"]
        if c.get("error"):
            lines.append(f"| {name} | 运行出错 | | | | | | | | | | | {c.get('duration_s')} 秒 |")
            continue
        lines.append(
            f"| {name} | {s.get(CORRECT, 0)} | {s.get(WRONG, 0)} | {s.get(MISSING, 0)} | "
            f"{s.get(PENDING, 0)} | {_pct(accuracy(s))} | {_source_cell(q)} | {q.get(SILENT, 0)} | "
            f"{len(c.get('pauses') or [])}"
            f"{'（卡住）' if c.get('stuck') else ''} | {c.get('readiness')} | "
            f"{st.get('ocr_calls', 0)}/{st.get('ocr_hits', 0)} | "
            f"{st.get('llm_calls', 0)}/{st.get('llm_hits', 0)} | {c.get('duration_s')} 秒 |"
        )
    lines += ["", f"**合计**：正确 {total[CORRECT]}，错误 {total[WRONG]}，缺失 {total[MISSING]}，"
              f"待定 {total[PENDING]}，准确率 {_pct(accuracy(total))}（待定不计分）；"
              f"来源错误 {_source_cell(total_q)}（填了预期来源的字段中），静默错误 {total_q[SILENT]}"
              "（审核页显示「正常」，但值错 / 应标待核实 / 回原文未命中）。", ""]

    lines.append(f"## 与上次运行对比{f'（{prev_run}）' if prev_run else ''}")
    lines.append("")
    if not prev_run:
        lines.append("没有上次运行的结果。")
    elif not changes:
        lines.append("没有字段的结论发生变化。")
    else:
        lines += ["| 案件 | 字段 | 上次 | 这次 |", "|---|---|---|---|"]
        lines += [f"| {x['case']} | `{x['key']}` | {x['before']} | {x['after']} |" for x in changes]
    lines.append("")

    for c in cases:
        lines.append(f"## {c.get('title') or c['case']}")
        lines.append("")
        if c.get("error"):
            lines += [f"运行出错：{c['error']}", ""]
            continue
        st = c.get("stats") or {}
        if st.get("llm_fallback"):
            lines += [f"DeepSeek 不可用，{st['llm_fallback']} 次 LLM 调用由备用模型回答（未录入缓存，结果不代表 DeepSeek）。", ""]
        if st.get("misses"):
            lines += [f"缓存未命中 {len(st['misses'])} 次（{'、'.join(sorted(set(st['misses'])))}）。", ""]
        for p in c.get("pauses") or []:
            ans = "，".join(f"{k}={v}" for k, v in p["answered"].items()) or "未作答，按默认继续"
            lines.append(f"- 暂停（{p['kind']}）：{_cell(p['question'])} → {ans}")
        if c.get("pauses"):
            lines.append("")
        bad = [
            r for r in c.get("results") or []
            if r["verdict"] in (WRONG, MISSING) or r.get("source_ok") is False or r.get("silent")
        ]
        if bad:
            lines += [
                "| 字段 | 结论 | 标准值 | 系统值 | 审核页显示 | 来源核对 | 系统出处 |",
                "|---|---|---|---|---|---|---|",
            ]
            lines += [
                f"| `{r['key']}` | {_label(r)} | {_cell(r['expected'])} | {_cell(r['actual'])} | "
                f"{r.get('shown') or '—'} | {_source_check(r)} | {_cell(r['src'])} |"
                for r in bad
            ]
        else:
            lines.append("计分字段全部正确。")
        pend = [r for r in c.get("results") or [] if r["verdict"] == PENDING]
        if pend:
            lines += ["", "待定（不计分）：" + "；".join(f"`{r['key']}`（{r['note']}）" for r in pend)]
        lines.append("")
    return "\n".join(lines)


def _source_check(r: dict[str, Any]) -> str:
    """来源核对一栏：预期来源 vs 回原文实际命中的文件。"""
    if r.get("source_ok") is None:
        return "—"
    found = r.get("verified_source")
    got = f"《{_cell(found)}》" if found else "原文未命中"  # 文件名可能含「|」，转义后再进表格
    return f"{'✓' if r['source_ok'] else '✗'} 应为「{_cell(r.get('expected_source'))}」，实际{got}"


def write_report(out_dir: str, meta: dict[str, Any], cases: list[dict[str, Any]],
                 prev: dict[str, Any] | None) -> str:
    """写 report.md、results.json、每个案件的 complaint.txt / fields.json，返回 report.md 路径。"""
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    changes = diff_runs(prev, cases)
    for c in cases:
        case_dir = os.path.join(out_dir, c["case"])
        os.makedirs(case_dir, mode=0o700, exist_ok=True)
        if c.get("complaint"):
            _write(os.path.join(case_dir, "complaint.txt"), c["complaint"])
        if c.get("fields"):
            _write(os.path.join(case_dir, "fields.json"), json.dumps(c["fields"], ensure_ascii=False, indent=1))
    slim = [{k: v for k, v in c.items() if k not in ("fields", "complaint")} for c in cases]
    _write(os.path.join(out_dir, "results.json"),
           json.dumps({"meta": meta, "cases": slim}, ensure_ascii=False, indent=1))
    path = os.path.join(out_dir, "report.md")
    _write(path, render_markdown(meta, cases, changes, prev.get("_run") if prev else None))
    return path


def _write(path: str, text: str) -> None:
    # 报告里有真实案件的取值：与案卷同样只许属主读写
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
