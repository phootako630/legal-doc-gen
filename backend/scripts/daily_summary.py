"""手动生成某天的运行小结（服务里每天会自动生成，见 app/services/daily_summary.py，#76）。

用法（在 backend/ 目录下）：
    python scripts/daily_summary.py [--date 2026-10-24] [--no-key-check]
Docker 部署时：docker compose exec backend python scripts/daily_summary.py
不给 --date 时生成昨天（北京时间）；已存在的同日小结会被覆盖。小结不含案件内容。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.daily_summary import BEIJING, run_for  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成某天的运行小结")
    parser.add_argument("--date", type=date.fromisoformat, help="日期 YYYY-MM-DD（默认昨天，北京时间）")
    parser.add_argument("--no-key-check", action="store_true", help="不检查 Key 连通性（不发外部请求）")
    args = parser.parse_args(argv)
    day = args.date or datetime.now(BEIJING).date() - timedelta(days=1)
    path = run_for(day, with_key_check=not args.no_key_check)
    with open(path, encoding="utf-8") as f:
        print(f.read())
    print(f"已写入：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
