"""LLM 裁判校准（#90 第 3 步）：人工结论当标准答案，算裁判的一致率、Cohen's kappa 和混淆矩阵。

真实案卷的配对与标注表含当事人信息，只放服务器 / 会话临时目录，不进仓库。写法见 case_eval/calibration.py。

用法（在 backend/ 目录下）：
    # 1. 生成标注表（不含裁判结论），人逐对填「一致 / 不一致 / 无法判断」
    python scripts/judge_calibrate.py sheet pairs.json 标注表.xlsx
    # 2. 跑裁判并对照人工结论出报告；裁判调用录在 --cache，重跑不花钱
    python scripts/judge_calibrate.py run pairs.json --labels 标注表.xlsx --cache cal-cache --out 校准报告.md
    python scripts/judge_calibrate.py run pairs.json --labels 标注表.xlsx --cache cal-cache --model qwen3.8-max
    python scripts/judge_calibrate.py run … --strict     # 只用缓存，不调用接口

只调用国内模型服务（DashScope，需要 DASHSCOPE_API_KEY），真实案卷调用前先征得同意。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.case_eval.cache import ReplayCache  # noqa: E402
from scripts.case_eval.calibration import judge_pairs, load_pairs, read_labels, render, write_sheet  # noqa: E402
from scripts.case_eval.judge import JUDGE_MODEL, call_judge  # noqa: E402


def _private(path: str) -> None:
    os.chmod(path, 0o600)  # 含真实案卷内容：只许属主读写


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 裁判校准")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sh = sub.add_parser("sheet", help="生成标注表")
    sh.add_argument("pairs")
    sh.add_argument("out")
    sh.add_argument("--seed", type=int, help="打乱行序的随机种子（固定后顺序可复现）")
    run = sub.add_parser("run", help="跑裁判并对照人工结论")
    run.add_argument("pairs")
    run.add_argument("--labels", help="填好的标注表；不给则只跑裁判、录缓存")
    run.add_argument("--cache", required=True, help="裁判调用的录制目录")
    run.add_argument("--model", default=JUDGE_MODEL)
    run.add_argument("--strict", action="store_true", help="只用缓存，不调用接口")
    run.add_argument("--out", required=True, help="校准报告（Markdown）")
    args = parser.parse_args()

    pairs = load_pairs(args.pairs)
    if args.cmd == "sheet":
        write_sheet(pairs, args.out, args.seed)
        _private(args.out)
        print(f"[校准] 标注表：{args.out}（{len(pairs)} 对）")
        return

    cache = ReplayCache(args.cache, mode="replay", strict=args.strict)
    ask = cache.wrap_judge(lambda messages: call_judge(messages, args.model), args.model)
    asyncio.run(judge_pairs(pairs, ask, args.model))
    if args.labels:
        labels = read_labels(args.labels)
        unknown = sorted(set(labels) - {p.id for p in pairs})
        if unknown:
            raise SystemExit(f"标注表里有配对文件中没有的编号：{'、'.join(unknown)}")
        for p in pairs:
            p.human = labels.get(p.id, "")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render(pairs, args.model))
    _private(args.out)
    st = cache.stats
    print(f"[校准] 模型调用 {st.judge_calls} 次，缓存 {st.judge_hits} 次；报告：{args.out}")


if __name__ == "__main__":
    main()
