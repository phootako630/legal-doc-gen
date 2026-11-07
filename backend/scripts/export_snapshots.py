"""线上案件快照 → eval 案卷（#157）：律师导出过起诉状的案件，变成和真实案卷集同样的目录，可严格回放、记 Langfuse。

快照含案卷原文：导出的案卷与真实案卷同样只放服务器私有目录或开发者电脑，绝不进仓库。

用法（在服务器上，用当前部署的代码；导出目录在容器里，再拷出来）：
    docker compose exec backend python scripts/export_snapshots.py /srv/backend/data/snapshots /tmp/exported
    docker compose exec backend python scripts/export_snapshots.py … --run <run_id>   # 只导出某个案件（可重复）
    docker compose cp backend:/tmp/exported ./exported-cases

导出后严格回放（不调用接口、零费用）：
    python scripts/eval_cases.py <导出目录> --strict [--langfuse]
已导出过的案件（目录已存在）跳过；律师还没导出起诉状的案件没有标准答案，不导出。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.case_eval.snapshot_export import completed_runs, export_case  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="线上案件快照导出成 eval 案卷")
    parser.add_argument("snapshots", help="快照目录（服务器容器里是 /srv/backend/data/snapshots）")
    parser.add_argument("out", help="导出到这个目录（每个案件一个子目录）")
    parser.add_argument("--run", action="append", default=[], help="只导出这个 run_id（可重复）")
    args = parser.parse_args()

    runs = args.run or completed_runs(args.snapshots)
    if not runs:
        raise SystemExit("没有可导出的案件（律师导出过起诉状的案件才有标准答案）")
    if not os.path.isdir(args.out):
        os.makedirs(args.out, mode=0o700)
        os.chmod(args.out, 0o700)
    done = skipped = 0
    for run_id in runs:
        try:
            info = export_case(args.snapshots, run_id, args.out)
        except ValueError as exc:
            print(f"[跳过] {exc}")
            skipped += 1
            continue
        done += 1
        edited = f"，律师改过 {len(info['edited'])} 个：{'、'.join(info['edited'])}" if info["edited"] else ""
        print(f"[导出] {info['case']}：{info['fields']} 个字段{edited}")
        for w in info["warnings"]:
            print(f"    ⚠️ {w}")
    print(f"共导出 {done} 个案件，跳过 {skipped} 个 → {args.out}")


if __name__ == "__main__":
    main()
