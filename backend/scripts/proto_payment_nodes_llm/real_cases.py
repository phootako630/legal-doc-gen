# 【原型，用完即弃】在服务器真实案卷集上对比付款节点：现有正则 vs「LLM 给结构 + 代码核不变量」。
#
# 回答三个问题：① 真实 OCR 文本上 LLM 一侧还是不是 0 错拆；② 正则认不出的条款 LLM 能多认出多少；
# ③ LLM 放弃时卡在哪条不变量上。没有人工标注的付款节点，以律师诉状为准：两种拆法各走一遍现有派生规则，
# 算出的「支付至 X%」（payable_ratio）和付款引用（payment_cite_location / payment_cite_text）与 expected.json 比。
#
# 用法（服务器上，backend/ 目录下；需要 DEEPSEEK_API_KEY，与线上同一个）：
#   python scripts/proto_payment_nodes_llm/real_cases.py /srv/eval-cases
# 流水线默认严格回放（不调用 OCR / 抽取，零费用；缓存对不上的案件跳过，加 --allow-live 才补调用）；只有付款条款拆分这一步调用 DeepSeek，每案 1 次，结果缓存在
# --cache（默认 /srv/eval-cases/_runs/proto-payment-cache），重跑不再收费。
#
# 保密：报告只有匿名编号（案件01…）、状态、计数和放弃原因的类别，不含条款原文、取值、文件名或当事人名称，
# 可以贴回对话。编号与案件目录的对照另存 *.map.json，只留在服务器上，不要外传。
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 与 eval_cases.py 相同：导入 app 前设置，不碰线上 data/、不写运行日志与案件快照
_TMP = tempfile.mkdtemp(prefix="proto-payment-")
os.environ["CHECKPOINT_DB_PATH"] = os.path.join(_TMP, "checkpoints.sqlite")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["RUN_LOG_ENABLED"] = "0"
os.environ["SNAPSHOT_ENABLED"] = "0"
os.environ["LLM_FALLBACK_MODEL"] = ""  # 只比 DeepSeek：失败记「调用失败」，不换成备用模型

from app.agent.graph import reset_graph  # noqa: E402
from app.services import derived_fields, validators  # noqa: E402
from app.services.derived_fields import apply_derived_fields  # noqa: E402
from app.services.payment_nodes import parse_payment_stages  # noqa: E402
from scripts.case_eval.runner import _as_of, _branch_table, load_expected, run_case  # noqa: E402
from scripts.case_eval.scoring import score_field  # noqa: E402
from scripts.proto_payment_nodes_llm.llm_parse import parse_with_llm  # noqa: E402

KEYS = ("payable_ratio", "payment_cite_location", "payment_cite_text")
_ORIGINAL = parse_payment_stages


def _reason_kind(reason: str) -> str:
    """放弃原因去掉具体内容，只留类别（「第2个节点编号「6.1」不在原文该处」→「节点编号「…」不在原文该处」）。"""
    s = re.sub(r"「[^」]*」", "「…」", reason or "")
    s = re.sub(r"第\d+个", "", s)
    return re.sub(r"\d+(?:\.\d+)?%?", "N", s)


def _with_stages(fields: dict, text: str, stages) -> dict:
    """把付款节点换成指定结果，再跑一遍派生规则（律师改过的值不动，和线上一样）。"""
    def fake(t):
        return list(stages) if t == text else _ORIGINAL(t)

    out = copy.deepcopy(fields)
    derived_fields.parse_payment_stages, validators.parse_payment_stages = fake, fake
    try:
        return apply_derived_fields(out)
    finally:
        derived_fields.parse_payment_stages = validators.parse_payment_stages = _ORIGINAL


async def one_case(case_dir: str, cache: Path, strict: bool = True) -> dict:
    row: dict = {}
    result = await run_case(case_dir, mode="replay", strict=strict)
    if result.get("error"):
        row["skip"] = "流水线回放失败：" + result["error"].split(":")[0]  # 只留异常类型，消息可能含案卷内容
        return row
    fields = result["fields"]
    text = derived_fields._case_payment(fields)[0]
    if not text or not text.strip():
        row["skip"] = "没有付款条款"
        return row
    rx = _ORIGINAL(text)
    try:
        lr = await parse_with_llm(text, cache)
        llm, reason = lr.stages, lr.reason
    except Exception as e:  # noqa: BLE001 — 原型：一个案件出错不影响其他案件
        llm, reason = [], f"调用失败：{type(e).__name__}"
    row["regex"] = "认出" if rx else "放弃"
    row["llm"] = "认出" if llm else "放弃"
    row["llm_reason"] = _reason_kind(reason) if not llm else ""
    if rx and llm:
        row["agree"] = [round(s.end, 2) for s in rx] == [round(s.end, 2) for s in llm]
    row["ratios"] = {"正则": [round(s.end, 2) for s in rx], "LLM": [round(s.end, 2) for s in llm]}

    expected = load_expected(case_dir)
    specs = expected.get("fields") or {}
    with _as_of(expected), _branch_table(case_dir):
        by = {"正则": _with_stages(fields, text, rx), "LLM": _with_stages(fields, text, llm)}
    row["scores"] = {}
    for key in KEYS:
        if key in specs:
            row["scores"][key] = {m: score_field(key, specs[key], f.get(key)).verdict for m, f in by.items()}
    # 自检：正则重算应与流水线原结果一致，不一致说明重算本身有偏差，这一案的下游比对不可信
    row["recompute_ok"] = all(
        (by["正则"].get(k) or {}).get("value") == (fields.get(k) or {}).get("value") for k in KEYS
    )
    return row


async def main() -> None:
    ap = argparse.ArgumentParser(description="真实案卷：付款节点正则 vs LLM + 不变量（原型）")
    ap.add_argument("root", help="案卷根目录，如 /srv/eval-cases")
    ap.add_argument("--cache", type=Path, help="LLM 返回缓存目录（默认 <root>/_runs/proto-payment-cache）")
    ap.add_argument("--out", type=Path, help="报告路径（默认 <root>/_runs/proto-payment-<时间>.md）")
    ap.add_argument("--allow-live", action="store_true",
                    help="流水线缓存未命中时允许调用 OCR / LLM 补上（会产生费用；默认严格回放，未命中的案件跳过）")
    ap.add_argument("--show-ratios", action="store_true", help="报告里列出各案的累计比例（默认不列，比例属合同条款）")
    args = ap.parse_args()

    root = Path(args.root)
    runs = root / "_runs"
    cache = args.cache or runs / "proto-payment-cache"
    out = args.out or runs / f"proto-payment-{datetime.now():%Y%m%d-%H%M}.md"
    names = sorted(
        n for n in os.listdir(root)
        if not n.startswith(("_", ".")) and (root / n / "expected.json").is_file()
    )
    if not names:
        raise SystemExit(f"{root} 下没有案件")

    rows, mapping = [], {}
    try:
        for i, n in enumerate(names, 1):
            cid = f"案件{i:02d}"
            mapping[cid] = n
            print(f"[{cid}] …", flush=True)
            row = await one_case(str(root / n), cache, strict=not args.allow_live)
            row["id"] = cid
            rows.append(row)
            print(f"[{cid}] {row.get('skip') or ('正则' + row['regex'] + ' / LLM' + row['llm'])}", flush=True)
    finally:
        await reset_graph()

    done = [r for r in rows if "skip" not in r]
    c = Counter()
    for r in done:
        c[f"正则{r['regex']}·LLM{r['llm']}"] += 1
        if "agree" in r:
            c["都认出·一致" if r["agree"] else "都认出·不一致"] += 1
    reasons = Counter(r["llm_reason"] for r in done if r["llm_reason"])
    score = Counter()
    for r in done:
        for key, v in r["scores"].items():
            for m, verdict in v.items():
                score[(key, m, verdict)] += 1
            if v["正则"] != v["LLM"]:
                score[(key, "变化", f"{v['正则']}→{v['LLM']}")] += 1

    L = [f"# 真实案卷：付款节点正则 vs LLM + 不变量（{datetime.now():%Y-%m-%d %H:%M}）", "",
         f"案件 {len(rows)} 个，可比 {len(done)} 个；跳过："
         + ("、".join(f"{k} {v}" for k, v in Counter(r['skip'] for r in rows if 'skip' in r).items()) or "无"),
         f"派生重算自检不一致：{sum(1 for r in done if not r['recompute_ok'])} 个（不为 0 时这些案件的下游比对不可信）", "",
         "## 拆节点", "", "| 情况 | 案件数 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(c.items())]
    L += ["", "## LLM 放弃原因", "", "| 原因类别 | 次数 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in reasons.most_common()] or ["| （无） | 0 |"]
    L += ["", "## 与律师诉状比对（走现有派生规则后）", "", "| 字段 | 方法 | 正确 | 错误 | 缺失 | 待定 |", "|---|---|---|---|---|---|"]
    for key in KEYS:
        for m in ("正则", "LLM"):
            L.append(f"| {key} | {m} | " + " | ".join(str(score[(key, m, v)]) for v in ("正确", "错误", "缺失", "待定")) + " |")
    changes = sorted((k, v) for k, v in score.items() if k[1] == "变化")
    L += ["", "两种方法结论不同的字段（正则 → LLM）：", ""]
    L += [f"- {k[0]}：{k[2]} × {v}" for k, v in changes] or ["- 无"]
    L += ["", "## 逐案", "", "| 案件 | 正则 | LLM | LLM 放弃原因 | 一致 | " + " | ".join(KEYS) + (" | 累计比例 |" if args.show_ratios else ""),
          "|---|---|---|---|---|" + "---|" * len(KEYS) + ("---|" if args.show_ratios else "")]
    for r in rows:
        if "skip" in r:
            L.append(f"| {r['id']} | {r['skip']} |  |  |  |" + " |" * len(KEYS) + (" |" if args.show_ratios else ""))
            continue
        sc = [f"{r['scores'][k]['正则']}→{r['scores'][k]['LLM']}" if k in r["scores"] else "—" for k in KEYS]
        agree = {True: "是", False: "**否**"}.get(r.get("agree"), "—")
        line = f"| {r['id']} | {r['regex']} | {r['llm']} | {r['llm_reason']} | {agree} | " + " | ".join(sc) + " |"
        if args.show_ratios:
            line += f" 正则 {r['ratios']['正则']} / LLM {r['ratios']['LLM']} |"
        L.append(line)
    report = "\n".join(L) + "\n"
    print("\n" + report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    mp = out.with_suffix(".map.json")
    mp.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(mp, 0o600)
    print(f"报告：{out}\n编号对照（只留在服务器、不要外传）：{mp}")


if __name__ == "__main__":
    asyncio.run(main())
