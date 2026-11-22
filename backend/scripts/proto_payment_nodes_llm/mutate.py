# 【原型，用完即弃】篡改测试：把 LLM 的正确输出故意改错，看不变量能不能拦住。
# 「漏过」= 改错后校验仍通过、且累计比例或编号与正确结果不同（会写进起诉状的错引用）。
#   python scripts/proto_payment_nodes_llm/mutate.py --cache <目录>
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.proto_payment_nodes_llm.corpus import all_cases  # noqa: E402
from scripts.proto_payment_nodes_llm.llm_parse import PROMPT, validate  # noqa: E402

_PCT = re.compile(r"\d+(?:\.\d+)?\s*[%％]")


def mutations(text: str, raw: dict):
    st = raw["stages"]
    pcts = sorted({p for p in _PCT.findall(text)})
    for i, s in enumerate(st):
        for p in pcts:  # 比例换成原文里另一个百分比（模型看错行）
            if p != s.get("pct_text"):
                m = copy.deepcopy(raw)
                m["stages"][i]["pct_text"] = p
                yield "比例看错", m
        m = copy.deepcopy(raw)  # 片段改一个字
        q = s.get("quote") or ""
        if len(q) > 3:
            m["stages"][i]["quote"] = q[:2] + ("付" if q[2] != "付" else "支") + q[3:]
            yield "片段改字", m
        m = copy.deepcopy(raw)  # 本期 / 累计 弄反
        flip = {"本期": "累计", "累计": "本期"}.get(s.get("basis"))
        if flip:
            m["stages"][i]["basis"] = flip
            yield "本期累计弄反", m
        m = copy.deepcopy(raw)  # 漏掉一个节点
        del m["stages"][i]
        yield "漏节点", m
        for j in range(len(st)):  # 编号张冠李戴
            if j != i and st[j].get("no") and st[j].get("no") != s.get("no"):
                m = copy.deepcopy(raw)
                m["stages"][i]["no"] = st[j]["no"]
                yield "编号错配", m
                break
    if raw.get("excluded"):
        m = copy.deepcopy(raw)
        m["excluded"] = []
        yield "排除项丢失", m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, type=Path)
    args = ap.parse_args()
    stats: dict[str, list[int]] = {}
    escapes = []
    for c in all_cases():
        key = hashlib.sha256(PROMPT.replace("{text}", c.text).encode("utf-8")).hexdigest()[:24]
        path = args.cache / f"{key}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        good = validate(c.text, raw)
        if not good.stages:
            continue
        sig = [(s.no, round(s.end, 2)) for s in good.stages]
        for kind, m in mutations(c.text, raw):
            r = validate(c.text, m)
            got = [(s.no, round(s.end, 2)) for s in r.stages]
            caught = not r.stages
            same = got == sig
            row = stats.setdefault(kind, [0, 0, 0])  # 拦下 / 结果不变 / 漏过
            row[0 if caught else 1 if same else 2] += 1
            if not caught and not same:
                escapes.append((c.id, kind, sig, got))
    print("| 篡改 | 拦下 | 改了但结果不变 | **漏过** |\n|---|---|---|---|")
    for k, (a, b, e) in stats.items():
        print(f"| {k} | {a} | {b} | {e} |")
    print("\n漏过明细：")
    for e in escapes:
        print(" ", e)


if __name__ == "__main__":
    main()
