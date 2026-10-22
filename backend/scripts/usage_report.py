"""按账号、按日期统计使用情况（读运行日志，不含任何案件内容）。

用法（在 backend/ 目录下）：
    python scripts/usage_report.py [运行日志路径] [--days 30]
日志路径默认取环境变量 RUN_LOG_PATH，未设置时为 data/run_log.jsonl；按天切分出的
run_log-YYYY-MM-DD.jsonl 一并读取（超过 RUN_LOG_RETENTION_DAYS 的已被服务自动删除）。
Docker 部署时：docker compose exec backend python scripts/usage_report.py --days 7

统计口径：分析 = 点「开始分析」启动的会话数；生成 = 生成起诉状次数；恢复 = 在断点处提交决定的次数；
反馈 = 提交的问题反馈数；token = DeepSeek 输入 + 输出 token；OCR 页 = 识别成功的扫描页数。
没有账号名的记录（未开启登录，或本功能上线前的旧日志）记在「（未登录 / 旧记录）」下。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_CST = timezone(timedelta(hours=8))
_NO_USER = "（未登录 / 旧记录）"
_COLUMNS = ("analyze", "generate", "resume", "feedback", "tokens", "ocr_pages")
_HEADERS = ("分析", "生成", "恢复", "反馈", "token", "OCR 页")


def summarize(lines: list[str], since: datetime | None) -> dict[tuple[str, str], dict[str, int]]:
    """把日志行汇总为 {(账号, 北京时间日期): {统计项: 数量}}；坏行跳过。"""
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(_COLUMNS, 0))
    for line in lines:
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if since is not None and ts < since:
            continue
        row = stats[(rec.get("user") or _NO_USER, ts.astimezone(_CST).date().isoformat())]
        event = rec.get("event")
        if event == "run_start":
            row["analyze"] += 1
        elif event == "lawyer_edits":
            row["generate"] += 1
        elif event == "resume":
            row["resume"] += 1
        elif event == "feedback":
            row["feedback"] += 1
        elif event == "llm_call":
            row["tokens"] += (rec.get("prompt_tokens") or 0) + (rec.get("completion_tokens") or 0)
        elif event == "ocr_batch":
            row["ocr_pages"] += rec.get("ok") or 0
    return {k: v for k, v in stats.items() if any(v.values())}


def log_files(path: str) -> list[str]:
    """当前日志及按天切分出的旧日志（与 app.services.run_log.log_files 同一命名规则）。"""
    stem, ext = os.path.splitext(path)
    rotated = sorted(glob.glob(f"{glob.escape(stem)}-????-??-??{glob.escape(ext)}"))
    return rotated + ([path] if os.path.exists(path) else [])


def main() -> int:
    parser = argparse.ArgumentParser(description="按账号统计使用情况")
    parser.add_argument(
        "log",
        nargs="?",
        default=os.getenv("RUN_LOG_PATH") or "data/run_log.jsonl",
    )
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
    stats = summarize(lines, since)
    if not stats:
        print("这段时间没有使用记录")
        return 0

    print("账号\t日期\t" + "\t".join(_HEADERS))
    totals: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_COLUMNS, 0))
    for (user, day), row in sorted(stats.items()):
        print(f"{user}\t{day}\t" + "\t".join(str(row[c]) for c in _COLUMNS))
        for c in _COLUMNS:
            totals[user][c] += row[c]
    print("\n合计")
    print("账号\t" + "\t".join(_HEADERS))
    for user, row in sorted(totals.items()):
        print(f"{user}\t" + "\t".join(str(row[c]) for c in _COLUMNS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
