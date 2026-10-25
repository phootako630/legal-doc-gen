# 每日运行小结（#76）：汇总前一天的运行日志，超过阈值列入「需要关注」，写到服务器文件。
#
# 只读运行日志（本身不含案件内容）并检查 Key 连通性，小结里只有次数、比例、金额和检查结果，
# 不出现 run_id 以外的任何案件信息，以后改发邮件也不用再脱敏。
# 「前一天」按北京时间算（中国不实行夏令时，固定 UTC+8，不依赖容器里的时区数据）。
from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app import config
from app.services import cost_summary, run_log

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))
_CHECK_INTERVAL_S = 3600
# Key 检查是阻塞的 HTTP 请求：放专用线程，不占默认线程池（见 CLAUDE.md「Error Handling」）
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-summary")


@dataclass(frozen=True)
class KeyCheck:
    name: str
    status: str  # 中文结果，如「正常」「被拒（HTTP 401）」
    alert: bool


@dataclass
class DayStats:
    day: date
    analyses: int = 0
    resumes: int = 0
    node_errors: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    ocr_requested: int = 0
    ocr_failed: int = 0
    llm_fallbacks: int = 0
    key_switches: int = 0
    cost: float = 0.0
    unpriced: bool = False


def day_lines(lines: list[str], day: date) -> list[str]:
    """只留北京时间落在 day 当天的日志行；坏行跳过。"""
    kept = []
    for line in lines:
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts.astimezone(BEIJING).date() == day:
            kept.append(line)
    return kept


def collect(lines: list[str], day: date, prices: dict) -> DayStats:
    """day 当天的各项计数；成本与错误类别复用 cost_summary 的口径。"""
    lines = day_lines(lines, day)
    s = DayStats(day=day)
    for line in lines:
        rec = json.loads(line)
        event = rec.get("event")
        if event == "run_start":
            s.analyses += 1
        elif event == "resume":
            s.resumes += 1
        elif event == "node" and rec.get("outcome") == "error":
            s.node_errors += 1
        elif event == "ocr_batch":
            s.ocr_requested += rec.get("requested") or 0
            s.ocr_failed += rec.get("failed") or 0
        elif event == "llm_fallback":
            s.llm_fallbacks += 1
    summary = cost_summary.summarize(lines, None, prices)
    s.errors = summary["errors"]
    s.key_switches = summary["key_switches"]
    s.cost = sum(r["cost"] for r in summary["runs"].values())
    s.unpriced = any(r["unpriced"] for r in summary["runs"].values())
    return s


def ocr_fail_rate(s: DayStats) -> float | None:
    return s.ocr_failed / s.ocr_requested if s.ocr_requested else None


def alerts(s: DayStats, keys: list[KeyCheck]) -> list[str]:
    """超过阈值的项（中文）；阈值见 config「每日运行小结」。"""
    out = []
    if s.node_errors > config.ALERT_MAX_NODE_ERRORS:
        out.append(f"分析中断 {s.node_errors} 次，超过 {config.ALERT_MAX_NODE_ERRORS} 次")
    rate = ocr_fail_rate(s)
    if rate is not None and s.ocr_requested >= config.ALERT_MIN_OCR_PAGES and rate > config.ALERT_MAX_OCR_FAIL_RATE:
        out.append(f"OCR 失败率 {rate:.0%}（{s.ocr_failed}/{s.ocr_requested} 页），超过 {config.ALERT_MAX_OCR_FAIL_RATE:.0%}")
    if s.llm_fallbacks > config.ALERT_MAX_LLM_FALLBACKS:
        out.append(f"DeepSeek 不可用、改用备用模型 {s.llm_fallbacks} 次，超过 {config.ALERT_MAX_LLM_FALLBACKS} 次")
    if s.key_switches:
        out.append(f"DashScope 免费 key 被拒、改用付费备用 key {s.key_switches} 次（开始产生费用）")
    if config.ALERT_MAX_DAILY_COST > 0 and s.cost > config.ALERT_MAX_DAILY_COST:
        out.append(f"当天成本 ¥{s.cost:.2f}，超过 ¥{config.ALERT_MAX_DAILY_COST:.2f}")
    out += [f"{k.name}：{k.status}" for k in keys if k.alert]
    return out


def check_keys() -> list[KeyCheck]:
    """用各 Key 请求一次模型列表（不计费），只报结果，不打印 key。
    被拒（401/403）或连不上才算需要关注；其他状态码可能只是接口不支持，只列出不报警。"""
    import requests

    targets = [
        ("DEEPSEEK_API_KEY", config.DEEPSEEK_API_KEY, config.DEEPSEEK_BASE_URL, True),
        ("DASHSCOPE_API_KEY", config.DASHSCOPE_API_KEY, config.LLM_FALLBACK_BASE_URL, True),
        ("DASHSCOPE_API_KEY_BACKUP", config.DASHSCOPE_API_KEY_BACKUP, config.LLM_FALLBACK_BASE_URL, False),
    ]
    results = []
    for name, key, base, required in targets:
        key = (key or "").strip()
        if not key:
            if required:
                results.append(KeyCheck(name, "未配置", True))
            continue
        try:
            resp = requests.get(
                f"{base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}, timeout=10
            )
        except requests.RequestException as exc:
            results.append(KeyCheck(name, f"连不上（{type(exc).__name__}）", True))
            continue
        if resp.status_code == 200:
            results.append(KeyCheck(name, "正常", False))
        elif resp.status_code in (401, 403):
            results.append(KeyCheck(name, f"被拒（HTTP {resp.status_code}）", True))
        else:
            results.append(KeyCheck(name, f"未能确认（HTTP {resp.status_code}）", False))
    return results


def render(s: DayStats, keys: list[KeyCheck], found: list[str], prices_date: str) -> str:
    rate = ocr_fail_rate(s)
    cost = f"¥{s.cost:.2f}" + ("（另有用量未配置单价）" if s.unpriced else "")
    lines = [f"# 运行小结 {s.day.isoformat()}（北京时间）", ""]
    if found:
        lines += ["## ⚠️ 需要关注", *[f"- {a}" for a in found], ""]
    else:
        lines += ["没有超过阈值的项。", ""]
    lines += [
        "## 当天数据",
        f"- 分析 {s.analyses} 次，律师在断点作答 {s.resumes} 次",
        f"- 分析中断（节点出错）{s.node_errors} 次",
        f"- OCR 请求 {s.ocr_requested} 页，失败 {s.ocr_failed} 页"
        + ("" if rate is None else f"（{rate:.0%}）"),
        f"- 改用备用模型 {s.llm_fallbacks} 次；改用付费备用 key {s.key_switches} 次",
        f"- 成本 {cost}（单价取值日期：{prices_date or '未填写'}）",
        "",
        "## 错误类别",
        *([f"- {k}：{v}" for k, v in sorted(s.errors.items(), key=lambda kv: -kv[1])] or ["- 无"]),
        "",
        "## Key 连通性（生成小结时）",
        *([f"- {k.name}：{k.status}" for k in keys] or ["- 未检查"]),
        "",
    ]
    return "\n".join(lines)


def summary_path(day: date, directory: str | None = None) -> str:
    return os.path.join(directory or config.DAILY_SUMMARY_DIR, f"{day.isoformat()}.md")


def deliver(day: date, text: str, found: list[str], directory: str | None = None) -> str:
    """写到服务器文件（目录 0700、文件 0600）。以后加邮件等通道也在这里。"""
    path = summary_path(day, directory)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if found:
        logger.warning("每日小结 %s 有 %d 项需要关注，见 %s", day.isoformat(), len(found), path)
    return path


def purge_old(directory: str | None = None, today: date | None = None) -> int:
    """小结与运行日志同一保留期（RUN_LOG_RETENTION_DAYS）。"""
    directory = directory or config.DAILY_SUMMARY_DIR
    today = today or datetime.now(BEIJING).date()
    cutoff = today - timedelta(days=config.RUN_LOG_RETENTION_DAYS)
    removed = 0
    for name in os.listdir(directory) if os.path.isdir(directory) else []:
        try:
            day = date.fromisoformat(name.removesuffix(".md"))
        except ValueError:
            continue
        if day < cutoff:
            os.remove(os.path.join(directory, name))
            removed += 1
    return removed


def _read_log_lines() -> list[str]:
    lines: list[str] = []
    for path in run_log.log_files():
        try:
            with open(path, encoding="utf-8") as f:
                lines.extend(f.read().splitlines())
        except OSError:
            continue
    return lines


def run_for(day: date, with_key_check: bool = True, directory: str | None = None) -> str:
    """生成 day 的小结并写文件，返回文件路径。"""
    stats = collect(_read_log_lines(), day, config.COST_PRICES)
    keys = check_keys() if with_key_check else []
    found = alerts(stats, keys)
    text = render(stats, keys, found, config.COST_PRICES_DATE)
    path = deliver(day, text, found, directory)
    purge_old(directory)
    return path


def due_day(now: datetime) -> date | None:
    """now（北京时间）已过 DAILY_SUMMARY_HOUR 点、前一天的小结还没写 → 返回前一天，否则 None。"""
    now = now.astimezone(BEIJING)
    if now.hour < config.DAILY_SUMMARY_HOUR:
        return None
    yesterday = now.date() - timedelta(days=1)
    return None if os.path.exists(summary_path(yesterday)) else yesterday


async def daily_loop(interval_s: float = _CHECK_INTERVAL_S) -> None:
    """每小时看一次该不该写前一天的小结；服务重启错过时间点也会补写。随服务关闭被取消。"""
    loop = asyncio.get_running_loop()
    while True:
        day = due_day(datetime.now(BEIJING))
        if day is not None:
            try:
                await loop.run_in_executor(_executor, run_for, day)
            except Exception as exc:  # noqa: BLE001 — 小结失败不能影响服务
                logger.warning("每日小结生成失败：%s", type(exc).__name__)
        await asyncio.sleep(interval_s)
