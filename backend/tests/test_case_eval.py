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
    content = messages[-1]["content"]
    if not json_mode:
        return "校验说明\n---HIGHLIGHT---\n- 无"
    if "核查案件材料是否齐全" in content:  # 清点提示词（prompt-a-checklist.md）
        return {"has_approval": True, "has_contract": True, "has_acceptance": False,
                "can_proceed": True, "missing": [], "notes": ""}
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
    assert first["stats"]["llm_calls"] >= 2 and "【待补充】" in first["complaint"]
    asyncio.run(reset_graph())  # 下一次 asyncio.run 是新的事件循环

    # 改规则后的典型用法：严格回放，不调用任何接口，结论不变
    monkeypatch.setattr(nodes_mod, "chat", _no_chat)
    again = asyncio.run(run_case(case, mode="replay", strict=True))
    assert again["error"] is None, again["error"]
    assert again["summary"] == first["summary"]
    assert again["stats"]["llm_calls"] == 0 and again["stats"]["llm_hits"] >= 2

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
