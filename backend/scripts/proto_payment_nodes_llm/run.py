# 【原型，用完即弃】付款节点：现有正则 vs「LLM 给结构 + 代码核不变量」，在同一批虚构条款上逐条对比。
#
# 用法（在 backend/ 下）：
#   python scripts/proto_payment_nodes_llm/run.py --cache <目录> [--offline] [--out report.md]
# 需要 DEEPSEEK_API_KEY（或备用模型的 DASHSCOPE_API_KEY）；--offline 只用缓存。
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.payment_nodes import PaymentStage, parse_payment_stages  # noqa: E402
from scripts.proto_payment_nodes_llm.corpus import Case, all_cases  # noqa: E402
from scripts.proto_payment_nodes_llm.llm_parse import parse_with_llm  # noqa: E402


def verdict(case: Case, stages: list[PaymentStage]) -> str:
    ends = [round(s.end, 2) for s in stages]
    if case.expect is None:
        return "正确放弃" if not stages else "错误"
    if not stages:
        return "放弃"
    return "正确" if any(ends == [float(x) for x in e] for e in case.expect) else "错误"


def fmt(stages: list[PaymentStage]) -> str:
    return " → ".join(f"{s.no or '·'}:{s.end:g}" for s in stages) or "（空）"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only")
    args = ap.parse_args()

    cases = [c for c in all_cases() if not args.only or args.only in c.id]
    sem = asyncio.Semaphore(4)

    async def one(c: Case):
        async with sem:
            try:
                return await parse_with_llm(c.text, args.cache, offline=args.offline)
            except Exception as e:  # 原型：调用失败记下原因，继续跑
                from scripts.proto_payment_nodes_llm.llm_parse import Result

                return Result([], f"调用失败：{e}", None)

    llm_results = await asyncio.gather(*(one(c) for c in cases))

    rows, tally = [], {}
    for c, lr in zip(cases, llm_results):
        rx = parse_payment_stages(c.text)
        vr, vl = verdict(c, rx), verdict(c, lr.stages)
        for side, v in (("正则", vr), ("LLM", vl)):
            tally.setdefault((c.group, side), {}).setdefault(v, 0)
            tally[(c.group, side)][v] += 1
        exp = "应放弃" if c.expect is None else " 或 ".join("/".join(f"{x:g}" for x in e) for e in c.expect)
        rows.append((c, exp, vr, fmt(rx), vl, fmt(lr.stages), lr.reason))

    lines = ["# 付款节点原型对比：正则 vs LLM + 不变量", "", "## 汇总", "",
             "| 组 | 方法 | 正确 | 正确放弃 | 放弃（交律师） | **错误（错拆）** |", "|---|---|---|---|---|---|"]
    for g in ("fixture", "unit", "heldout", "heldout2"):
        for side in ("正则", "LLM"):
            t = tally.get((g, side), {})
            lines.append(f"| {g} | {side} | {t.get('正确', 0)} | {t.get('正确放弃', 0)} | {t.get('放弃', 0)} | {t.get('错误', 0)} |")
    lines += ["", "## 逐条", "", "| 案例 | 期望累计 | 正则 | 正则结果 | LLM | LLM结果 | LLM放弃原因 |", "|---|---|---|---|---|---|---|"]
    for c, exp, vr, rx, vl, lx, why in rows:
        mark = " ⚠" if vr != vl else ""
        lines.append(f"| {c.id}{mark} | {exp} | {vr} | {rx} | {vl} | {lx} | {why} |")
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
