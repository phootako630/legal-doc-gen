# 每日运行小结（#76）：汇总前一天的运行日志，超过阈值列入「需要关注」，写到服务器文件。
#
# 只读运行日志（本身不含案件内容）并检查 Key 连通性，小结里只有次数、比例、金额和检查结果，
# 不出现 run_id 以外的任何案件信息，以后改发邮件也不用再脱敏。
# 「前一天」按北京时间算（中国不实行夏令时，固定 UTC+8，不依赖容器里的时区数据）。
from __future__ import annotations

import asyncio
import contextlib
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
# Key 检查是阻塞的 HTTP 请求：放专用线程，不占默认线程池（见 CODING_STANDARDS.md「阻塞调用与线程池」）
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
    client_errors: int = 0
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
        elif event == "client_error":
            s.client_errors += 1
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
        f"- 前端报错（未捕获异常、接口 5xx）{s.client_errors} 次",
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
    """写到服务器文件（目录 0700、文件 0600）。以后加邮件等通道也在这里。
    先写临时文件再原子替换：写到一半失败（如磁盘满）不会留下残缺的正式文件，
    due_days 看不到它，下一轮会重试（#103 评审）。"""
    path = summary_path(day, directory)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
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
    """读全部运行日志。读的时候恰好按天切分（北京时间 8 点 = UTC 0 点，正是切分时刻），
    列出的当前日志会被改名、打不开：重新列一次再读。仍读不全就抛异常，本轮不写小结、下轮再试，
    免得写出缺数据的正式小结后再也不补（#103 评审）。"""
    last: OSError | None = None
    for _ in range(2):
        lines: list[str] = []
        try:
            for path in run_log.log_files():
                with open(path, encoding="utf-8") as f:
                    lines.extend(f.read().splitlines())
            return lines
        except OSError as exc:
            last = exc
    raise last  # type: ignore[misc]


def run_for(day: date, with_key_check: bool = True, directory: str | None = None) -> str:
    """生成 day 的小结并写文件，返回文件路径。"""
    stats = collect(_read_log_lines(), day, config.COST_PRICES)
    keys = check_keys() if with_key_check else []
    found = alerts(stats, keys)
    text = render(stats, keys, found, config.COST_PRICES_DATE)
    path = deliver(day, text, found, directory)
    purge_old(directory)
    return path


def _written_days(directory: str) -> list[date]:
    """已写好的小结日期（只认 YYYY-MM-DD.md，写到一半的 .tmp 不算）。"""
    days = []
    for name in os.listdir(directory) if os.path.isdir(directory) else []:
        if name.endswith(".md"):
            with contextlib.suppress(ValueError):
                days.append(date.fromisoformat(name[:-3]))
    return days


def due_days(now: datetime) -> list[date]:
    """该写而没写的日期，从早到晚。now（北京时间）没过 DAILY_SUMMARY_HOUR 点时昨天还不算到期。
    从最近一份已写小结的次日补到昨天（服务停了几天也逐日补齐，#103 评审），最多回溯运行日志的保留期；
    一份都没写过（刚上线）只写昨天，不给上线前的日子补一堆空小结。"""
    now = now.astimezone(BEIJING)
    last = now.date() - timedelta(days=1 if now.hour >= config.DAILY_SUMMARY_HOUR else 2)
    written = _written_days(config.DAILY_SUMMARY_DIR)
    if not written:
        yesterday = now.date() - timedelta(days=1)
        return [yesterday] if last == yesterday else []
    floor = now.date() - timedelta(days=config.RUN_LOG_RETENTION_DAYS)
    start = max(max(written) + timedelta(days=1), floor)
    days = []
    day = start
    while day <= last:
        if not os.path.exists(summary_path(day)):
            days.append(day)
        day += timedelta(days=1)
    return days


async def daily_loop(interval_s: float = _CHECK_INTERVAL_S) -> None:
    """每小时看一次有没有该写的小结；服务重启错过时间点也会逐日补写。随服务关闭被取消。
    Key 连通性只在最近一天的小结里查（补写的旧日子查了也不代表当时），某天失败就停在那天，下轮再试。"""
    loop = asyncio.get_running_loop()
    while True:
        days = due_days(datetime.now(BEIJING))
        for day in days:
            try:
                await loop.run_in_executor(_executor, run_for, day, day == days[-1])
            except Exception as exc:  # noqa: BLE001 — 小结失败不能影响服务
                logger.warning("每日小结生成失败：%s", type(exc).__name__)
                break
        await asyncio.sleep(interval_s)
