"""线上案件快照 → 抽取质量信号报告（#74）：律师改得最多的字段、静默错误、状态 / 通道 / confidence 与改动率、断点选择、就绪度。

报告只有字段 key、类别与计数，不含任何取值，可以把汇总数字贴到 issue。

用法（在服务器上，用当前部署的代码）：
    docker compose exec backend python scripts/snapshot_quality.py /srv/backend/data/snapshots
    docker compose exec backend python scripts/snapshot_quality.py /srv/backend/data/snapshots --since 2026-11-12
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.case_eval.snapshot_quality import load_cases, render, summarize  # noqa: E402

_CST = timezone(timedelta(hours=8))


def main() -> None:
    parser = argparse.ArgumentParser(description="线上案件快照的抽取质量信号报告")
    parser.add_argument("snapshots", help="快照目录（服务器容器里是 /srv/backend/data/snapshots）")
    parser.add_argument("--since", help="只统计这天（含）以后开始分析的案件，格式 YYYY-MM-DD")
    parser.add_argument("--out", help="报告另存到这个文件（默认只打印）")
    args = parser.parse_args()

    cases = load_cases(args.snapshots)
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=_CST).timestamp()
        cases = [c for c in cases if (c.get("started_at") or 0) >= start]
    report = render(summarize(cases))
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)


if __name__ == "__main__":
    main()
