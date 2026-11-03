# LLM 裁判（#90 第 2 步）：expected.json 里配了 "match": "judge" 的字段，问另一个模型
# 「系统句」和「律师句」在法律意义上是否一致。评分标准见 judge_rubrics.py。
#
# 几条约束：
#   - 裁判与抽取用不同的模型（抽取是 DeepSeek，裁判默认用 DashScope 的通义千问），免得模型给自己的输出打高分；
#     EVAL_JUDGE_MODEL 可覆盖，只用国内模型服务。
#   - 能用代码判的不问模型：两边逐字相同 → 一致；一边没有这句（或系统句还是【待补充】）→ 不一致。
#   - 结构化输出：只认 {"reason": "...", "verdict": "一致 / 不一致 / 无法判断"}，格式不对一律按「无法判断」。
#   - 裁判出错、严格回放时缓存未命中，都记为「未裁判」，不影响其他字段的打分，也不让整个案件出错。
#   - 调用经 ReplayCache 录制 / 回放（cache/judge/），改报告后重跑不花钱；结论只列出，校准前不计入准确率。
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

from app import config
from scripts.case_eval.cache import CacheMiss
from scripts.case_eval.judge_rubrics import JUDGE_PRINCIPLES
from scripts.case_eval.scoring import _blank, _norm_text

JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", config.LLM_FALLBACK_MODEL)

AGREE = "一致"
DISAGREE = "不一致"
UNSURE = "无法判断"
VERDICTS = (AGREE, DISAGREE, UNSURE)
NOT_JUDGED = "未裁判"  # 裁判出错或缓存未命中：没有结论

BY_CODE = "代码"  # 结论由代码直接给出，没有问模型

_OUTPUT_RULE = (
    '只输出一个 JSON 对象：{"reason": "一句话理由，不一致时指出哪里不同", '
    '"verdict": "一致" 或 "不一致" 或 "无法判断"}。先想清楚理由，再给结论。'
)

Ask = Callable[[list[dict]], Awaitable[Any]]


def build_messages(criteria: str, lawyer: Any, system: Any) -> list[dict]:
    """裁判的提示词：通用原则 + 输出格式放系统消息，字段标准与两句话放用户消息。"""
    rules = "\n".join(f"{i}. {p}" for i, p in enumerate(JUDGE_PRINCIPLES, 1))
    return [
        {"role": "system", "content": f"{rules}\n\n{_OUTPUT_RULE}"},
        {"role": "user", "content": f"字段标准：{criteria}\n\n律师句：{lawyer}\n\n系统句：{system}"},
    ]


def parse_verdict(raw: Any) -> tuple[str, str]:
    """模型的回答 → (结论, 理由)。不是 JSON 对象、结论不在三个选项里、没给理由，都按「无法判断」
    （没有理由的结论没法复核，#153 评审）。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return UNSURE, f"裁判回答不是 JSON：{raw[:80]}"
    if not isinstance(raw, dict):
        return UNSURE, "裁判回答格式不对"
    verdict = str(raw.get("verdict") or "").strip()
    reason = str(raw.get("reason") or "").strip()[:200]
    if verdict not in VERDICTS:
        return UNSURE, f"裁判结论不在可选项里（{verdict[:20] or '空'}）{'：' + reason if reason else ''}"
    if not reason:
        return UNSURE, f"裁判没有给出理由（原结论「{verdict}」），按无法判断"
    return verdict, reason


def precheck(expected: Any, actual: Any) -> tuple[str, str] | None:
    """不需要问模型的情况；返回 None 表示要交给模型。"""
    lawyer_blank, system_blank = _blank(expected), _blank(actual)
    # 系统句还是【待补充】：起诉状里留着待补充，不论律师写没写都算不一致（#153 评审：不能当成「两边都没有」）
    if system_blank and actual is not None and str(actual).strip():
        return DISAGREE, "系统句仍是【待补充】"
    if lawyer_blank and system_blank:
        return AGREE, "两边都没有这句"
    if system_blank:
        return DISAGREE, "系统没有生成这句"
    if lawyer_blank:
        return DISAGREE, "律师没有写这句，系统写了"
    if _norm_text(expected) == _norm_text(actual):
        return AGREE, "两句逐字相同"
    return None


async def call_judge(messages: list[dict], model: str = JUDGE_MODEL) -> Any:
    """真实调用裁判模型（DashScope 兼容接口，JSON 输出，温度 0）。"""
    from openai import AsyncOpenAI

    key = (config.DASHSCOPE_API_KEY or "").strip()
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用裁判模型")
    client = AsyncOpenAI(api_key=key, base_url=config.LLM_FALLBACK_BASE_URL,
                         timeout=config.LLM_TIMEOUT, max_retries=1)
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    return resp.choices[0].message.content or ""


async def judge_one(criteria: str, expected: Any, actual: Any, ask: Ask,
                    model: str = JUDGE_MODEL) -> tuple[str, str, str]:
    """裁判一对句子 → (结论, 理由, 判定方)。代码能判的不问模型；出错、缓存未命中记「未裁判」。"""
    pre = precheck(expected, actual)
    if pre:
        return pre[0], pre[1], BY_CODE
    try:
        raw = await ask(build_messages(criteria, expected, actual))
    except CacheMiss:
        return NOT_JUDGED, "裁判缓存未命中（严格回放不调用外部服务）", ""
    except Exception as exc:  # noqa: BLE001 — 裁判出错只影响这一对
        return NOT_JUDGED, f"裁判调用失败：{type(exc).__name__}: {exc}"[:200], ""
    verdict, reason = parse_verdict(raw)
    return verdict, reason, model


async def judge_results(results: list[Any], ask: Ask, model: str = JUDGE_MODEL) -> None:
    """给 match == judge 的字段填上裁判结论（judge_verdict / judge_reason / judge_by），就地修改。"""
    for r in results:
        if r.match != "judge" or r.judge_verdict:
            continue
        r.judge_verdict, r.judge_reason, r.judge_by = await judge_one(
            r.criteria, r.expected, r.actual, ask, model
        )
