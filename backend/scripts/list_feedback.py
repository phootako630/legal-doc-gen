"""列出律师在审核页提交的反馈（按时间顺序）。

用法（在 backend/ 目录下）：
    python scripts/list_feedback.py [checkpoint 库路径] [--run RUN_ID]
库路径默认取环境变量 CHECKPOINT_DB_PATH，未设置时为 data/checkpoints.sqlite。
Docker 部署时：docker compose exec backend python scripts/list_feedback.py

反馈与案件数据同库，超过 CASE_RETENTION_DAYS 会随案件一起被清除，请在保留期内查看。
文字说明可能含案件内容，查看后不要复制到 issue / 聊天记录里。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timedelta, timezone

_CATEGORY = {
    "wrong_value": "值不对",
    "missing": "缺失 / 没抽到",
    "wrong_source": "来源或页码不对",
    "other": "其他",
}
_CST = timezone(timedelta(hours=8))


def main() -> int:
    parser = argparse.ArgumentParser(description="列出律师反馈")
    parser.add_argument(
        "db",
        nargs="?",
        default=os.getenv("CHECKPOINT_DB_PATH") or "data/checkpoints.sqlite",
    )
    parser.add_argument("--run", help="只看某个会话（run_id）的反馈")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"找不到案件库：{args.db}")
        return 1
    # 只读打开，不影响正在运行的服务
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        sql = "SELECT run_id, field_key, category, comment, created_at FROM feedback"
        params: tuple = ()
        if args.run:
            sql += " WHERE run_id = ?"
            params = (args.run,)
        rows = conn.execute(sql + " ORDER BY created_at, id", params).fetchall()
    except sqlite3.OperationalError:
        print("库里还没有反馈表（服务尚未以新版本启动过）。")
        return 0
    finally:
        conn.close()

    if not rows:
        print("暂无反馈。")
        return 0
    print(f"共 {len(rows)} 条反馈\n")
    for run_id, field_key, category, comment, created_at in rows:
        ts = datetime.fromtimestamp(created_at, _CST).strftime("%Y-%m-%d %H:%M")
        print(
            f"{ts}  会话 {run_id[:8]}  {field_key or '（整体）'}  "
            f"[{_CATEGORY.get(category, category)}]"
        )
        if comment:
            print(f"    {comment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
