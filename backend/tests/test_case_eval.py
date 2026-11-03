# 案件 eval（scripts/case_eval）：打分归一化、录制 / 回放缓存、整条链路（虚构案件，mock 掉 LLM）。
import asyncio
import json
import os

import fitz
import pytest

from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from scripts.case_eval import cache as cache_mod
from scripts.case_eval.cache import CacheMiss, ReplayCache, patched
from scripts.case_eval.report import diff_runs, find_previous, write_report
from scripts.case_eval.runner import run_case
from scripts.case_eval.scoring import CORRECT, MISSING, PENDING, WRONG, parse_spec, score_field


# ── 打分 ──────────────────────────────────────────────────────────────────────
def _node(value, src="《审批表》"):
    return {"value": value, "src": src}


@pytest.mark.parametrize(
    "spec, actual, verdict",
    [
        (841534, "841,534.00元", CORRECT),  # 金额：千分位、单位不算差异
        (757380.6, "757,380.60", CORRECT),
        (84153.4, "84,153.41", WRONG),  # 差一分即错
        ("2024年4月15日", "2024年04月15日", CORRECT),  # 日期：补零不算差异
        ("2024年4月15日", "2024-04-16", WRONG),
        ("某某电梯（中国）有限公司", "某某电梯(中国)有限公司", CORRECT),  # 全半角括号
        ("某某电梯有限公司", "某某电梯有限公司 分公司", WRONG),
        ({"value": "最高不超过安装费总额的10%", "match": "contains"}, "，最高不超过安装费总额的10%", CORRECT),
        (None, None, CORRECT),  # 应留空
        (None, "张三", WRONG),
        ("苏州市吴江区", None, MISSING),
        ("苏州市吴江区", "", MISSING),
        ("昆山市人民法院", "【待补充】人民法院", MISSING),  # 拼着待补充的派生值算缺失，不算取错
        (None, "【待补充】", CORRECT),
        ({"pending": "确认单第 1 题"}, "随便", PENDING),
    ],
)
def test_score_field(spec, actual, verdict):
    assert score_field("k", spec, _node(actual)).verdict == verdict


def test_score_field_absent_node_counts_as_missing():
    assert score_field("k", "某值", None).verdict == MISSING


def test_parse_spec_infers_match():
    assert parse_spec(18)["match"] == "amount"
    assert parse_spec("2020年7月30日")["match"] == "date"
    assert parse_spec("2020年7月30日签订")["match"] == "text"
    assert parse_spec(None)["match"] == "absent"
    with pytest.raises(ValueError):
        parse_spec({"value": "x", "match": "fuzzy"})


# ── 缓存 ──────────────────────────────────────────────────────────────────────
def test_chat_cache_records_then_replays(tmp_path):
    calls = []

    async def real(messages, json_mode=False):
        calls.append(messages)
        return {"answer": len(calls)}

    msgs = [{"role": "user", "content": "抽取"}]
    rec = ReplayCache(str(tmp_path), mode="replay")
    assert asyncio.run(rec.wrap_chat(real)(msgs, json_mode=True)) == {"answer": 1}
    assert rec.stats.llm_calls == 1 and rec.stats.misses == ["LLM"]

    rep = ReplayCache(str(tmp_path), mode="replay", strict=True)
    assert asyncio.run(rep.wrap_chat(real)(msgs, json_mode=True)) == {"answer": 1}
    assert rep.stats.llm_hits == 1 and len(calls) == 1  # 没再调用

    # json_mode 不同即是不同请求：严格回放下未命中报错
    with pytest.raises(CacheMiss):
        asyncio.run(rep.wrap_chat(real)(msgs, json_mode=False))

    live = ReplayCache(str(tmp_path), mode="live")
    assert asyncio.run(live.wrap_chat(real)(msgs, json_mode=True)) == {"answer": 2}  # live 不读缓存


def test_fallback_answers_are_not_recorded(tmp_path):
    # DeepSeek 宕机时由备用模型回答：结果照常返回，但不录进 DeepSeek 的回放缓存
    from app.services import llm_client

    async def served_by_backup(messages, json_mode=False):
        llm_client._served_model.set("qwen-test")
        return "备用回答"

    rec = ReplayCache(str(tmp_path), mode="replay")
    msgs = [{"role": "user", "content": "x"}]
    assert asyncio.run(rec.wrap_chat(served_by_backup)(msgs)) == "备用回答"
    assert rec.stats.llm_fallback == 1
    with pytest.raises(CacheMiss):
        asyncio.run(ReplayCache(str(tmp_path), mode="replay", strict=True).wrap_chat(served_by_backup)(msgs))


def test_ocr_cache_skips_empty_results(tmp_path):
    results = iter(["", "第一页文字"])
    rec = ReplayCache(str(tmp_path), mode="replay")
    rec.ocr_available = True
    ocr = rec.wrap_ocr(lambda img: next(results))
    assert ocr("aW1n") == ""  # 接口异常时的空串不录
    assert ocr("aW1n") == "第一页文字"
    rep = ReplayCache(str(tmp_path), mode="replay", strict=True)
    assert rep.wrap_ocr(lambda img: "不应调用")("aW1n") == "第一页文字"


def test_ocr_miss_without_key_is_explicit(tmp_path):
    rec = ReplayCache(str(tmp_path), mode="replay")
    rec.ocr_available = False
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        rec.wrap_ocr(lambda img: "x")("aW1n")


def test_patched_restores_entry_points(tmp_path):
    before = (nodes_mod.chat, cache_mod.ocr_engine._ocr_image_b64)
    with patched(ReplayCache(str(tmp_path))):
        assert nodes_mod.chat is not before[0]
    assert (nodes_mod.chat, cache_mod.ocr_engine._ocr_image_b64) == before


# ── 整条链路（虚构案件）──────────────────────────────────────────────────────
def _text_pdf(path, text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontname="china-s", fontsize=10)
    doc.save(path)


_FIELDS = {
    "plaintiff_name_final": {"value": "某某电梯有限公司", "src": "《审批表.pdf》"},
    "defendant_name": {"value": "某某置业有限公司", "src": "《审批表.pdf》"},
    "total_amount": {"value": "1,000,000.00", "src": "《审批表.pdf》"},
    "paid_amount": {"value": "900,000.00", "src": "《审批表.pdf》"},
    "unpaid_amount": {"value": "100,000.00", "src": "《审批表.pdf》"},
    "contract_no": {"value": "XX0001", "src": "《审批表.pdf》"},
}


async def _fake_chat(messages, json_mode=False):
    if not json_mode:
        return "校验说明\n---HIGHLIGHT---\n- 无"
    return json.loads(json.dumps(_FIELDS))


async def _no_chat(messages, json_mode=False):
    raise AssertionError("严格回放不应调用 LLM")


def _make_case(root):
    case = os.path.join(root, "虚构案")
    os.makedirs(os.path.join(case, "materials"))
    _text_pdf(os.path.join(case, "materials", "某某审批表.pdf"),
              "收款不良合同诉讼审批表 合同号 XX0001 合同总额 1,000,000.00 已付 900,000.00 未付 100,000.00")
    _text_pdf(os.path.join(case, "materials", "安装合同.pdf"),
              "电梯安装合同 合同编号 XX0001 甲方 某某置业有限公司 乙方 某某电梯有限公司 合同总价 1000000 元")
    expected = {
        "title": "虚构案",
        "fields": {
            "contract_no": "XX0001",
            "total_amount": 1000000,
            "unpaid_amount": 100000,
            "defendant_name": "某某置业有限公司",
            "court_district": {"pending": "测试"},
        },
    }
    with open(os.path.join(case, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump(expected, fh, ensure_ascii=False)
    return case


def test_run_case_live_then_strict_replay(tmp_path, monkeypatch):
    case = _make_case(str(tmp_path))

    monkeypatch.setattr(nodes_mod, "chat", _fake_chat)
    first = asyncio.run(run_case(case, mode="replay"))
    assert first["error"] is None, first["error"]
    assert first["summary"] == {CORRECT: 4, WRONG: 0, MISSING: 0, PENDING: 1}
    # 只有抽取一次（清点由代码判定）；校验说明在 eval 里不生成（只供展示，且会让规则改动后回放未命中）
    assert first["stats"]["llm_calls"] == 1 and "【待补充】" in first["complaint"]
    asyncio.run(reset_graph())  # 下一次 asyncio.run 是新的事件循环

    # 改规则后的典型用法：严格回放，不调用任何接口，结论不变
    monkeypatch.setattr(nodes_mod, "chat", _no_chat)
    again = asyncio.run(run_case(case, mode="replay", strict=True))
    assert again["error"] is None, again["error"]
    assert again["summary"] == first["summary"]
    assert again["stats"]["llm_calls"] == 0 and again["stats"]["llm_hits"] == 1

    # 报告：results.json + report.md；第二次运行能找到上一次并对比
    runs = str(tmp_path / "_runs")
    meta = {"run": "20260101-000000", "mode": "replay", "llm_model": "m", "ocr_model": "o"}
    write_report(os.path.join(runs, meta["run"]), meta, [first], None)
    prev = find_previous(runs, "20260102-000000")
    assert prev and prev["_run"] == "20260101-000000"
    worse = json.loads(json.dumps(again, ensure_ascii=False))
    worse["results"][0]["verdict"] = WRONG
    assert diff_runs(prev, [worse]) == [
        {"case": "虚构案", "key": "contract_no", "before": CORRECT, "after": WRONG}
    ]
    report = write_report(os.path.join(runs, "20260102-000000"),
                          dict(meta, run="20260102-000000"), [worse], prev)
    text = open(report, encoding="utf-8").read()
    assert "contract_no" in text and "上次" in text
    assert oct(os.stat(report).st_mode & 0o777) == "0o600"


def test_run_case_reports_missing_expected(tmp_path):
    case = tmp_path / "空案"
    (case / "materials").mkdir(parents=True)
    result = asyncio.run(run_case(str(case)))
    assert result["error"] and "expected.json" in result["error"]


def test_as_of_pins_generation_date():
    # expected.json 的 as_of：按律师起诉当时判分（质保期是否届满等），不随运行日期变化（#138）
    from datetime import date

    from app.services import derived_fields
    from scripts.case_eval.runner import _as_of

    with _as_of({"as_of": "2025-03-01"}):
        assert derived_fields._today() == date(2025, 3, 1)
    assert derived_fields._today() != date(2025, 3, 1)
    with _as_of({}):
        assert derived_fields._today() != date(2025, 3, 1)


def test_judge_spec_uses_default_or_custom_criteria():
    # 规则拼接的句子可交给裁判（#90）：默认标准见 judge_rubrics.py，expected.json 可覆盖
    from scripts.case_eval.judge_rubrics import JUDGE_CRITERIA

    parsed = parse_spec({"value": "故原告向某市人民法院提起诉讼。", "match": "judge"}, "jurisdiction_text")
    assert parsed["match"] == "judge" and parsed["criteria"] == JUDGE_CRITERIA["jurisdiction_text"]
    custom = parse_spec({"value": "x", "match": "judge", "criteria": "法院相同即可"}, "jurisdiction_text")
    assert custom["criteria"] == "法院相同即可"


def test_only_allowlisted_rule_sentences_can_be_judged():
    # 白名单（#152 评审）：电话、联系人、台数、比例、规则拼的算式等不在白名单的字段，写了 criteria 也不交给裁判
    for key in ("plaintiff_phone", "contacts", "elevator_qty", "retention_ratio", "payable_ratio", "paid_note"):
        parsed = parse_spec({"value": "x", "match": "judge", "criteria": "大致相同即可"}, key)
        assert parsed["match"] == "text" and parsed["criteria"] is None and "严格比对" in parsed["warning"]


def test_verbatim_field_never_judged():
    # 证据原文类字段配成 judge：退回逐字比对并警告，不交给裁判（CLAUDE.md「证据原文保留」）
    parsed = parse_spec({"value": "虚构置业有限公司", "match": "judge"}, "defendant_name")
    assert parsed["match"] == "text" and "逐字" in parsed["warning"]
    wrong = score_field("defendant_name", {"value": "虚构置业有限公司", "match": "judge"},
                        {"value": "虚构置业公司", "src": "《合同》"})
    assert wrong.verdict == WRONG and "逐字" in wrong.warning


def test_misconfigured_judge_warning_shown_in_report(tmp_path):
    # 退回逐字比对后结论是「正确」也要在报告里看到警告（#152 评审）
    r = score_field("plaintiff_phone", {"value": "0755-1234", "match": "judge"}, {"value": "0755-1234", "src": "x"})
    assert r.verdict == CORRECT
    case = {"case": "虚构案", "title": "虚构案", "results": [r.as_dict()], "summary": {}, "quality": {},
            "stats": {}, "pauses": []}
    meta = {"run": "20260101-000000", "mode": "replay", "llm_model": "m", "ocr_model": "o"}
    path = write_report(str(tmp_path), meta, [case], None)
    body = open(path, encoding="utf-8").read()
    assert "## 配置警告" in body and "plaintiff_phone" in body and "严格比对" in body


def test_payment_rubric_consistent_with_principles():
    # 付款节点标准与通用原则不冲突：非欠款节点的数字不参与比较（#152 评审）
    from scripts.case_eval.judge_rubrics import JUDGE_CRITERIA, JUDGE_PRINCIPLES

    assert "不参与比较" in JUDGE_CRITERIA["payment_cite_text"]
    assert any("不参与比较" in p for p in JUDGE_PRINCIPLES)


def test_judge_field_listed_not_scored_until_wired():
    r = score_field("handover_text", {"value": "已全部移交物业", "match": "judge"},
                    {"value": "已移交给物业", "src": "按律师规则"})
    assert r.verdict == PENDING and r.match == "judge" and "裁判" in r.note
