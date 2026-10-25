"""成本、耗时与错误汇总（读运行日志，不含任何案件内容，#75）。

用法（在 backend/ 目录下）：
    python scripts/cost_report.py [运行日志路径] [--days 30]
Docker 部署时：docker compose exec backend python scripts/cost_report.py --days 7

输出：每案成本、成本最高的节点、各模型用量与金额、各错误类别次数、断点等待时长中位数与放弃率、
付费备用 key 切换次数。金额按 config.COST_PRICES（.env 的 COST_PRICES）换算，单价没配时只列用量。
口径：LLM 按每次调用的输入 / 输出 token 计；OCR 按识别成功的页数计；放弃率 = 过期时仍停在断点的
会话数 ÷ 停过断点的会话数（同一会话停多次只算一次）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import COST_PRICES, COST_PRICES_DATE  # noqa: E402
from scripts.usage_report import log_files  # noqa: E402

_NO_NODE = "（节点外）"
_NO_PRICE = "未配置单价"


def _llm_cost(model: str, prompt: int, completion: int, prices: dict) -> float | None:
    p = prices.get(model)
    if not isinstance(p, dict) or "input" not in p or "output" not in p:
        return None
    return (prompt * p["input"] + completion * p["output"]) / 1_000_000


def summarize(lines: list[str], since: datetime | None, prices: dict) -> dict[str, Any]:
    """把日志行汇总成报告需要的各项；坏行跳过。金额为 None 表示有用量但缺单价。"""
    runs: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost": 0.0, "unpriced": False})
    nodes: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost": 0.0, "unpriced": False, "ms": 0})
    models: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0, "unpriced": False}
    )
    errors: Counter[str] = Counter()
    waits: list[float] = []
    interrupted: set[str] = set()
    abandoned: set[str] = set()
    key_switches = 0
    ocr_pages = 0

    def add_cost(run_id: str | None, node: str, cost: float | None, model: str | None = None) -> None:
        buckets = [runs[run_id or "（无编号）"], nodes[node]]
        if model is not None:
            buckets.append(models[model])
        for bucket in buckets:
            if cost is None:
                bucket["unpriced"] = True
            else:
                bucket["cost"] += cost

    for line in lines:
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if since is not None and ts < since:
            continue
        event, run_id, node = rec.get("event"), rec.get("run_id"), rec.get("node") or _NO_NODE
        if event == "llm_call":
            model = rec.get("model") or "未知模型"
            prompt, completion = rec.get("prompt_tokens") or 0, rec.get("completion_tokens") or 0
            m = models[model]
            m["calls"] += 1
            m["prompt"] += prompt
            m["completion"] += completion
            add_cost(run_id, node, _llm_cost(model, prompt, completion, prices), model)
        elif event == "ocr_batch":
            ok = rec.get("ok") or 0
            ocr_pages += ok
            price = prices.get("ocr_page")
            add_cost(run_id, node, ok * price if isinstance(price, (int, float)) else (0.0 if not ok else None))
            timeouts, limited = rec.get("timeouts") or 0, rec.get("rate_limited") or 0
            errors["ocr_timeout"] += timeouts
            errors["ocr_rate_limit"] += limited
            # failed 是失败页总数：除去超时、限流，其余都是普通 OCR 失败（节点照常结束，不走 node 错误）
            errors["ocr_failed"] += max(0, (rec.get("failed") or 0) - timeouts - limited)
        elif event == "node":
            nodes[rec.get("node") or _NO_NODE]["ms"] += rec.get("duration_ms") or 0
            if rec.get("outcome") == "error":
                errors[rec.get("error_category") or "internal"] += 1
            elif rec.get("outcome") == "interrupt" and run_id:
                interrupted.add(run_id)
        elif event == "resume" and isinstance(rec.get("wait_s"), (int, float)):
            waits.append(float(rec["wait_s"]))
        elif event == "abandoned" and run_id:
            abandoned.add(run_id)
        elif event == "dashscope_key_switch":
            key_switches += 1

    return {
        "runs": dict(runs),
        "nodes": dict(nodes),
        "models": dict(models),
        "errors": {k: v for k, v in errors.items() if v},
        "wait_median_s": statistics.median(waits) if waits else None,
        "resumes": len(waits),
        "interrupted": len(interrupted),
        # 放弃的会话一定停过断点；旧日志可能缺 interrupt 记录，分母取两者并集
        "abandoned": len(abandoned),
        "abandon_rate": len(abandoned) / len(interrupted | abandoned) if (interrupted | abandoned) else None,
        "key_switches": key_switches,
        "ocr_pages": ocr_pages,
    }


def _money(bucket: dict[str, Any]) -> str:
    text = f"¥{bucket['cost']:.2f}"
    return f"{text}（另有用量{_NO_PRICE}）" if bucket["unpriced"] else text


def render(s: dict[str, Any], prices_date: str) -> str:
    out = [f"单价取值日期：{prices_date or '未填写（.env 的 COST_PRICES_DATE）'}", ""]
    runs = sorted(s["runs"].items(), key=lambda kv: kv[1]["cost"], reverse=True)
    out.append(f"## 每案成本（{len(runs)} 个会话，按金额从高到低）")
    out += [f"{run_id}\t{_money(b)}" for run_id, b in runs] or ["（无）"]
    out.append("")
    nodes = sorted(s["nodes"].items(), key=lambda kv: kv[1]["cost"], reverse=True)
    out.append("## 各节点成本与耗时")
    out.append("节点\t金额\t累计耗时（秒）")
    out += [f"{n}\t{_money(b)}\t{b['ms'] / 1000:.0f}" for n, b in nodes]
    if nodes:
        out.append(f"成本最高的节点：{nodes[0][0]}")
    out.append("")
    out.append("## 各模型用量")
    out.append("模型\t调用次数\t输入 token\t输出 token\t金额")
    out += [
        f"{m}\t{v['calls']}\t{v['prompt']}\t{v['completion']}\t{_money(v)}"
        for m, v in sorted(s["models"].items())
    ]
    out.append(f"OCR 识别成功页数：{s['ocr_pages']}；付费备用 key 切换：{s['key_switches']} 次")
    out.append("")
    out.append("## 错误类别")
    out += [f"{k}\t{v}" for k, v in sorted(s["errors"].items(), key=lambda kv: -kv[1])] or ["（无）"]
    out.append("")
    median = s["wait_median_s"]
    rate = s["abandon_rate"]
    out.append("## 断点等待")
    out.append(f"律师作答 {s['resumes']} 次，等待时长中位数："
               f"{'—' if median is None else f'{median / 60:.1f} 分钟'}")
    out.append(f"停过断点的会话中，过期仍未作答（放弃）：{s['abandoned']} 个，放弃率 "
               f"{'—' if rate is None else f'{rate * 100:.0f}%'}")
    return "\n".join(out)


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
