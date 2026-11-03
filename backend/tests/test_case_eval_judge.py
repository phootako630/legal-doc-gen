# LLM 裁判（#90 第 2 步）：提示词、结构化输出解析、代码预判、录制回放、出错不影响打分、报告单列。
# 全部用假的裁判回答，不调用任何模型。
import asyncio
import json

from scripts.case_eval import judge as judge_mod
from scripts.case_eval.cache import ReplayCache
from scripts.case_eval.judge import (
    AGREE,
    BY_CODE,
    DISAGREE,
    NOT_JUDGED,
    UNSURE,
    build_messages,
    judge_results,
    parse_verdict,
)
from scripts.case_eval.judge_rubrics import JUDGE_CRITERIA, JUDGE_PRINCIPLES
from scripts.case_eval.report import write_report
from scripts.case_eval.scoring import PENDING, score_field, summarize

LAWYER = "故原告依据合同第十五条约定，向某市人民法院提起诉讼。"
SYSTEM = "根据合同第十五条，原告向某市人民法院起诉。"


def _judged(expected, actual, answer=None, exc=None):
    """一个 jurisdiction_text 字段交给假裁判，返回 (结果, 裁判收到的提示词列表)。"""
    asked = []

    async def ask(messages):
        asked.append(messages)
        if exc:
            raise exc
        return answer

    return _judged_with(ask, expected, actual), asked


def test_prompt_carries_principles_criteria_and_both_sentences():
    msgs = build_messages(JUDGE_CRITERIA["jurisdiction_text"], LAWYER, SYSTEM)
    system, user = msgs[0]["content"], msgs[1]["content"]
    assert all(p in system for p in JUDGE_PRINCIPLES) and '"verdict"' in system
    assert JUDGE_CRITERIA["jurisdiction_text"] in user and LAWYER in user and SYSTEM in user


def test_agree_and_disagree_from_judge():
    r, asked = _judged(LAWYER, SYSTEM, {"reason": "法院与依据相同", "verdict": "一致"})
    assert (r.judge_verdict, r.judge_reason, r.judge_by) == (AGREE, "法院与依据相同", judge_mod.JUDGE_MODEL)
    assert len(asked) == 1
    r, _ = _judged(LAWYER, "向另一市人民法院起诉。", json.dumps({"reason": "法院不同", "verdict": "不一致"}))
    assert (r.judge_verdict, r.judge_reason) == (DISAGREE, "法院不同")


def test_unsure_and_malformed_answers_become_unsure():
    r, _ = _judged(LAWYER, SYSTEM, {"reason": "看不出依据", "verdict": "无法判断"})
    assert r.judge_verdict == UNSURE
    # 结论不在三个选项里、不是 JSON、不是对象：都按「无法判断」，不当成一致
    assert parse_verdict({"verdict": "基本一致", "reason": "x"})[0] == UNSURE
    assert parse_verdict("一致")[0] == UNSURE
    assert parse_verdict(["一致"])[0] == UNSURE
    assert parse_verdict({"reason": "x"})[0] == UNSURE
    # 有结论没理由：没法复核，按「无法判断」（#153 评审）
    assert parse_verdict({"verdict": "一致"})[0] == UNSURE
    assert parse_verdict({"verdict": "不一致", "reason": "  "})[0] == UNSURE


def test_code_decides_identical_or_missing_without_asking():
    r, asked = _judged(LAWYER, f" {LAWYER} ", {"verdict": "不一致"})
    assert (r.judge_verdict, r.judge_by) == (AGREE, BY_CODE) and not asked
    r, asked = _judged(LAWYER, None, {"verdict": "一致"})
    assert (r.judge_verdict, r.judge_by) == (DISAGREE, BY_CODE) and not asked
    r, asked = _judged(LAWYER, "【待补充】人民法院", {"verdict": "一致"})
    assert r.judge_verdict == DISAGREE and not asked
    # 律师没写、系统句是【待补充】：仍是不一致，不能当「两边都没有」（#153 评审）
    r, asked = _judged(None, "【待补充】人民法院", {"verdict": "一致"})
    assert (r.judge_verdict, r.judge_reason) == (DISAGREE, "系统句仍是【待补充】") and not asked
    r, _ = _judged(None, None)
    assert r.judge_verdict == AGREE


def test_judge_failure_marks_field_only():
    r, _ = _judged(LAWYER, SYSTEM, exc=RuntimeError("未配置 DASHSCOPE_API_KEY"))
    assert r.judge_verdict == NOT_JUDGED and "DASHSCOPE_API_KEY" in r.judge_reason


def test_judge_verdict_not_counted():
    # 裁判说一致也不计入正确：校准前（第 3 步）只列出
    r, _ = _judged(LAWYER, SYSTEM, {"reason": "法院相同", "verdict": "一致"})
    assert r.verdict == PENDING and summarize([r])[PENDING] == 1
    assert r.as_dict()["judge_verdict"] == AGREE


def test_record_then_strict_replay(tmp_path):
    calls = []

    async def real(messages):
        calls.append(messages)
        return {"reason": "相同", "verdict": "一致"}

    live = ReplayCache(str(tmp_path), mode="live")
    r1 = _judged_with(live.wrap_judge(real, "judge-model"))
    assert r1.judge_verdict == AGREE and live.stats.judge_calls == 1 and len(calls) == 1

    strict = ReplayCache(str(tmp_path), mode="replay", strict=True)
    r2 = _judged_with(strict.wrap_judge(real, "judge-model"))
    assert r2.judge_verdict == AGREE and strict.stats.judge_hits == 1 and len(calls) == 1

    # 换了裁判模型 → 缓存键不同；严格回放未命中记「未裁判」，不调用、不让案件出错
    other = ReplayCache(str(tmp_path), mode="replay", strict=True)
    r3 = _judged_with(other.wrap_judge(real, "another-model"))
    assert r3.judge_verdict == NOT_JUDGED and "未命中" in r3.judge_reason
    assert other.stats.misses == ["裁判"] and len(calls) == 1


def _judged_with(ask, expected=LAWYER, actual=SYSTEM):
    r = score_field("jurisdiction_text", {"value": expected, "match": "judge"}, {"value": actual, "src": "按律师规则"})
    asyncio.run(judge_results([r], ask))
    return r


def test_report_lists_judge_section(tmp_path):
    r, _ = _judged(LAWYER, SYSTEM, {"reason": "法院相同", "verdict": "一致"})
    case = {"case": "c", "title": "虚构案", "results": [r.as_dict()], "summary": summarize([r]),
            "stats": {"judge_calls": 1, "judge_hits": 0}, "duration_s": 1}
    meta = {"run": "r", "mode": "replay", "llm_model": "deepseek-flash", "ocr_model": "o", "judge_model": "qwen"}
    text = (tmp_path / "out" / "report.md")
    write_report(str(tmp_path / "out"), meta, [case], None)
    body = text.read_text(encoding="utf-8")
    assert "## LLM 裁判（校准前不计分）" in body and "法院相同" in body and "裁判：qwen" in body
    assert "同一个模型" not in body
    # 裁判与抽取同一模型时警告
    write_report(str(tmp_path / "out"), {**meta, "judge_model": "deepseek-flash"}, [case], None)
    assert "同一个模型" in text.read_text(encoding="utf-8")
    # 抽取改由备用模型回答、备用模型就是裁判：也警告（#153 评审）
    fallback = {**case, "stats": {**case["stats"], "llm_fallback": 2}}
    write_report(str(tmp_path / "out"), {**meta, "llm_fallback_model": "qwen"}, [fallback], None)
    assert "备用模型 qwen 回答，与裁判是同一个模型" in text.read_text(encoding="utf-8")
    write_report(str(tmp_path / "out"), {**meta, "llm_fallback_model": "qwen"}, [case], None)
    assert "同一个模型" not in text.read_text(encoding="utf-8")


def test_no_judge_section_without_judge_fields(tmp_path):
    r = score_field("total_amount", 100, {"value": "100", "src": "x"})
    case = {"case": "c", "results": [r.as_dict()], "summary": summarize([r]), "stats": {}, "duration_s": 1}
    meta = {"run": "r", "mode": "replay", "llm_model": "m", "ocr_model": "o"}
    write_report(str(tmp_path), meta, [case], None)
    assert "LLM 裁判" not in (tmp_path / "report.md").read_text(encoding="utf-8")
