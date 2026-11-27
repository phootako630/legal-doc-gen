"""LLM 裁判校准（#90 第 3 步）：人工结论当标准答案，算裁判的一致率、Cohen's kappa 和混淆矩阵。

真实案卷的配对与标注表含当事人信息，只放服务器 / 会话临时目录，不进仓库。写法见 case_eval/calibration.py。

用法（在 backend/ 目录下）：
    # 0. 从 eval 运行结果导出真实配对（可并入已有配对文件，编号不重复）
    python scripts/judge_calibrate.py export _runs/20261124-0930/results.json pairs.json --base 旧pairs.json
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
import json
import os
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.case_eval.cache import ReplayCache  # noqa: E402
from scripts.case_eval.calibration import (  # noqa: E402
    judge_pairs,
    load_pairs,
    pairs_from_results,
    read_labels,
    render,
    write_sheet,
)
from scripts.case_eval.judge import JUDGE_MODEL, call_judge  # noqa: E402


def _write_private(path: str, write) -> None:
    """
    含真实案卷内容的输出：在同目录用 0600 新建临时文件（mkstemp 建立时即只许属主读写），写完再 os.replace 覆盖目标。
    权限从建立起就收紧；写到一半出错时临时文件删掉，原有文件（如律师已填的标注表）不受影响（#180 评审）。
    """
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=os.path.splitext(path)[1])
    os.close(fd)
    try:
        write(tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _write_text(path: str, text: str) -> None:
    def write(tmp: str) -> None:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)

    _write_private(path, write)


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 裁判校准")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="从 eval 的 results.json 导出真实配对")
    ex.add_argument("results", nargs="+", help="一个或多个 results.json")
    ex.add_argument("out", help="输出的配对文件")
    ex.add_argument(
        "--base", help="已有配对文件：校验后原样保留在前面，新配对编号与内容都不与它重复；多次导出请用它串起来"
    )
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

    if args.cmd == "export":
        base: list[dict] = []
        if args.base:
            try:
                load_pairs(args.base)  # 白名单、编号唯一：坏数据在这里就报，不等到生成标注表
            except (ValueError, KeyError) as exc:
                raise SystemExit(f"[校准] --base 配对文件有误：{exc}") from exc
            with open(args.base, encoding="utf-8") as fh:
                base = json.load(fh)
        cases: list[dict] = []
        for path in args.results:
            with open(path, encoding="utf-8") as fh:
                cases += json.load(fh).get("cases", [])
        new, dup = pairs_from_results(cases, base)
        if not base and not new:
            raise SystemExit("[校准] 没有可导出的配对：这些运行结果里没有交给裁判、两边都有内容的字段")
        _write_text(args.out, json.dumps(base + new, ensure_ascii=False, indent=2))
        skipped = f"，跳过重复 {dup} 对" if dup else ""
        print(f"[校准] 配对：{args.out}（原有 {len(base)} 对，新增 {len(new)} 对{skipped}）")
        return

    pairs = load_pairs(args.pairs)
    if args.cmd == "sheet":
        _write_private(args.out, lambda tmp: write_sheet(pairs, tmp, args.seed))
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
    _write_text(args.out, render(pairs, args.model))
    st = cache.stats
    print(f"[校准] 模型调用 {st.judge_calls} 次，缓存 {st.judge_hits} 次；报告：{args.out}")


if __name__ == "__main__":
    main()
