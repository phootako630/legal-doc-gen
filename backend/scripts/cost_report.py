"""成本、耗时与错误汇总（读运行日志，不含任何案件内容，#75）。

用法（在 backend/ 目录下）：
    python scripts/cost_report.py [运行日志路径] [--days 30]
Docker 部署时：docker compose exec backend python scripts/cost_report.py --days 7

输出：每案成本、成本最高的节点、各模型用量与金额、各错误类别次数、断点等待时长中位数与放弃率、
付费备用 key 切换次数。金额按 config.COST_PRICES（.env 的 COST_PRICES）换算，单价没配时只列用量。
口径与计算见 app/services/cost_summary.py（每日小结共用）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import COST_PRICES, COST_PRICES_DATE  # noqa: E402
from app.services.cost_summary import render, summarize  # noqa: E402
from scripts.usage_report import log_files  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="成本、耗时与错误汇总")
    parser.add_argument("log", nargs="?", default=os.getenv("RUN_LOG_PATH") or "data/run_log.jsonl")
    parser.add_argument("--days", type=int, default=30, help="只看最近 N 天（默认 30；0 = 全部）")
    args = parser.parse_args()

    files = log_files(args.log)
    if not files:
        print(f"找不到运行日志：{args.log}")
        return 1
    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days > 0 else None
    lines: list[str] = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            lines.extend(f.read().splitlines())
    print(render(summarize(lines, since, COST_PRICES), COST_PRICES_DATE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
