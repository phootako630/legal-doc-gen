# 案件 eval 的来源与静默错误评分（#59）：材料、取值均为虚构，不调用真实 OCR / LLM
import asyncio
import json
import os

import fitz
import pytest

from app.agent import nodes as nodes_mod
from app.agent.graph import reset_graph
from scripts.case_eval.report import _source_check, diff_runs, find_previous, write_report
from scripts.case_eval.runner import run_case
from scripts.case_eval.scoring import (
    CORRECT,
    REVIEW_FIELD_KEYS,
    SHOWN_HIDDEN,
    SHOWN_MISSING,
    MISSING,
    SHOWN_CONFLICT,
    SHOWN_NORMAL,
    SHOWN_REVIEW,
    SILENT,
    SOURCE_WRONG,
    WRONG,
    parse_spec,
    score_field,
    summarize_quality,
)

_FILES = [
    {"filename": "虚构审批表.pdf", "text": "诉讼审批表 合同号 XG-0815 合同总额 894934 签约时间 2024-03-01"},
    {"filename": "虚构安装合同.pdf", "text": "电梯安装合同 合同编号 XG-0815 签订日期：2024年3月1日 甲方 虚构置业有限公司"},
    {"filename": "虚构战略合作协议.pdf", "text": "战略合作协议 签订日期：2023年1月10日"},
]


def _node(value, src):
    return {"value": value, "src": src}


# ── 打分：四条路径 ───────────────────────────────────────────────────────────
def test_strategic_date_taken_as_main_contract_date():
    # 值取成了战略合同的日期，模型还自称来自安装合同：值错、来源错，审核页却显示正常 → 静默错误
    spec = {"value": "2024年3月1日", "source": "安装合同"}
    r = score_field("contract_sign_date", spec, _node("2023年1月10日", "《虚构安装合同.pdf》第3页"), _FILES)
    assert r.verdict == WRONG
    assert r.verified_source == "虚构战略合作协议.pdf"  # 回原文实际命中的文件，不信 src 自述
    assert r.source_ok is False and r.shown == SHOWN_NORMAL and r.silent


def test_same_value_but_wrong_file_cited():
    # 合同号两份材料里都有，值对，但出处写的是审批表、标准答案要求合同
    r = score_field("contract_no", {"value": "XG-0815", "source": "安装合同"},
                    _node("XG-0815", "《虚构审批表.pdf》合同号"), _FILES)
    assert r.verdict == CORRECT
    assert r.verified_source == "虚构审批表.pdf" and r.source_ok is False
    assert not r.silent  # 值没错、也不要求待核实，只记来源错误


def test_unanchored_value_shown_normal_is_silent():
    # 值哪份材料里都找不到，出处却没带「原文未找到，待核实」（审核页显示正常）
    r = score_field("defendant_name", {"value": "虚构置业发展有限公司", "source": "安装合同"},
                    _node("虚构置业发展有限公司", "《虚构安装合同.pdf》第1页"), _FILES)
    assert r.verdict == CORRECT
    assert r.verified_source is None and r.source_ok is False
    assert r.shown == SHOWN_NORMAL and r.silent


def test_correct_value_and_source():
    r = score_field("contract_sign_date", {"value": "2024年3月1日", "source": "安装合同"},
                    _node("2024年3月1日", "《虚构安装合同.pdf》盖章页"), _FILES)
    assert r.verdict == CORRECT and r.source_ok is True and not r.silent
    assert r.verified_source == "虚构安装合同.pdf"


# ── 打分：兼容与边界 ─────────────────────────────────────────────────────────
def test_old_spec_only_scores_value():
    # 旧 expected.json（不填 source / review）：只比值，不查来源；值错且显示正常仍算静默错误
    ok = score_field("contract_no", "XG-0815", _node("XG-0815", "《虚构审批表.pdf》"), _FILES)
    assert ok.verdict == CORRECT and ok.source_ok is None and not ok.silent
    bad = score_field("contract_no", "XG-0815", _node("XG-0816", "《虚构审批表.pdf》"))
    assert bad.verdict == WRONG and bad.silent


def test_review_expectation():
    spec = {"value": "第5条", "review": "待核实"}
    flagged = score_field("payment_clause_location", spec, _node("第5条", "《合同.pdf》（扫描件，OCR识别，请核实）"))
    assert flagged.shown == SHOWN_REVIEW and not flagged.silent
    unflagged = score_field("payment_clause_location", spec, _node("第5条", "《合同.pdf》第3页"))
    assert unflagged.verdict == CORRECT and unflagged.silent


def test_conflict_and_missing_are_not_silent():
    r = score_field("total_amount", 894934, _node(900000, "《虚构审批表.pdf》"), conflict_keys={"total_amount"})
    assert r.verdict == WRONG and r.shown == SHOWN_CONFLICT and not r.silent
    m = score_field("total_amount", {"value": 894934, "source": "审批表"}, _node(None, ""), _FILES)
    assert m.verdict == MISSING and m.source_ok is None and not m.silent


def test_quality_summary_and_bad_spec():
    results = [
        score_field("contract_no", {"value": "XG-0815", "source": "安装合同"}, _node("XG-0815", "《虚构审批表.pdf》"), _FILES),
        score_field("contract_no", {"value": "XG-0815", "source": "审批表"}, _node("XG-0815", "《虚构审批表.pdf》"), _FILES),
        score_field("contract_no", "XG-0815", _node("XG-0816", "《虚构审批表.pdf》")),
    ]
    assert summarize_quality(results) == {SOURCE_WRONG: 1, "来源检查": 2, SILENT: 1}
    with pytest.raises(ValueError, match="review"):
        parse_spec({"value": "x", "review": "正常"})
    with pytest.raises(ValueError, match="source"):
        parse_spec({"value": "x", "source": ""})


# ── 审核页显示状态与前端一致（#100 评审）──────────────────────────────────────
def test_hidden_field_is_not_silent():
    # 三个来源口径的台数不在审核表格里，律师看不到：值错也不算「显示正常」的静默错误
    r = score_field("elevator_qty_by_approval", 6, _node(8, "《虚构审批表.pdf》"))
    assert r.verdict == WRONG and r.shown == SHOWN_HIDDEN and not r.silent


def test_contacts_scored_like_review_page():
    # 联系人是数组：按审核页的汇总写法「姓名（电话）」取值，有值显示正常
    contacts = [{"name": {"value": "虚构张三"}, "phone": {"value": "13800000000"}}, {"name": None}]
    ok = score_field("contacts", "虚构张三（13800000000）", contacts)
    assert ok.verdict == CORRECT and ok.shown == SHOWN_NORMAL and not ok.silent
    bad = score_field("contacts", "虚构李四（13900000000）", contacts)
    assert bad.verdict == WRONG and bad.silent
    empty = score_field("contacts", "虚构张三（13800000000）", [{"name": None, "phone": None}])
    assert empty.verdict == MISSING and empty.shown == SHOWN_MISSING and not empty.silent


def test_review_field_keys_match_frontend():
    # eval 在后端容器里跑、读不到前端，展示字段清单只能抄一份：两边改岔了这里会失败
    import re

    path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "lib", "field-map.ts")
    with open(path, encoding="utf-8") as fh:
        ts = fh.read()
    body = ts[ts.index("export const reviewFieldKeys"):]
    body = body[body.index("[") + 1 : body.index("];")]
    assert tuple(re.findall(r"'([a-z_]+)'", body)) == REVIEW_FIELD_KEYS


def test_source_cell_escapes_table_separator():
    # 文件名含「|」时不能把报告的明细表拆成多列
    r = {"source_ok": False, "expected_source": "安装合同", "verified_source": "虚构|审批表.pdf"}
    assert "|" not in _source_check(r)


# ── 整条链路：标准答案 → 跑案件 → 报告 → 与上次对比 ─────────────────────────
def _text_pdf(path, text):
    # 用文本框自动换行：单行 insert_text 超出页宽的部分会被截掉、提取不到
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontname="china-s", fontsize=10)
    doc.save(path)


_EXTRACTED = {
    # 正确值、正确来源
    "total_amount": {"value": 894934, "src": "《虚构审批表.pdf》合同总额"},
    "paid_amount": {"value": 638667.2, "src": "《虚构审批表.pdf》已付款"},
    "unpaid_amount": {"value": 256266.8, "src": "《虚构审批表.pdf》未付款"},
    # 同值引错文件
    "contract_no": {"value": "XG-0815", "src": "《虚构审批表.pdf》合同号"},
    # 值错、显示正常
    "project_site": {"value": "虚构市梧桐区88号", "src": "《虚构审批表.pdf》项目地址"},
}


async def _fake_chat(messages, json_mode=False):
    if not json_mode:
        return "说明\n---HIGHLIGHT---\n- 无"
    return json.loads(json.dumps(_EXTRACTED, ensure_ascii=False))


def _make_case(root):
    case = os.path.join(root, "虚构案")
    os.makedirs(os.path.join(case, "materials"))
    _text_pdf(os.path.join(case, "materials", "虚构审批表.pdf"),
              "收款不良合同诉讼审批表 合同号 XG-0815 合同总额 894934 已付款 638667.2 未付款 256266.8 项目地址 虚构市梧桐区88号")
    _text_pdf(os.path.join(case, "materials", "虚构安装合同.pdf"),
              "电梯安装合同 合同编号 XG-0815 甲方 虚构置业有限公司 乙方 虚构电梯有限公司 "
              "第五条 付款方式 第十五条 争议解决 签订日期：2024年3月1日")  # 字数够，按文字版处理
    expected = {
        "title": "虚构案（来源评分）",
        "fields": {
            "total_amount": {"value": 894934, "source": "审批表"},
            "contract_no": {"value": "XG-0815", "source": "安装合同"},
            "project_site": "虚构市枫林区99号",
            "unpaid_amount": 256266.8,
        },
    }
    with open(os.path.join(case, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump(expected, fh, ensure_ascii=False)
    return case


def test_case_eval_reports_source_and_silent_errors(tmp_path, monkeypatch):
    case = _make_case(str(tmp_path))
    monkeypatch.setattr(nodes_mod, "chat", _fake_chat)
    result = asyncio.run(run_case(case, mode="replay"))
    asyncio.run(reset_graph())
    assert result["error"] is None, result["error"]
    by_key = {r["key"]: r for r in result["results"]}
    assert by_key["total_amount"]["source_ok"] is True
    assert by_key["contract_no"]["verdict"] == CORRECT and by_key["contract_no"]["source_ok"] is False
    assert by_key["project_site"]["verdict"] == WRONG and by_key["project_site"]["silent"]
    assert result["quality"] == {SOURCE_WRONG: 1, "来源检查": 2, SILENT: 1}

    runs = str(tmp_path / "_runs")
    meta = {"run": "20260101-000000", "mode": "replay", "llm_model": "m", "ocr_model": "o"}
    report = write_report(os.path.join(runs, meta["run"]), meta, [result], None)
    text = open(report, encoding="utf-8").read()
    assert "| 来源错误 | 静默错误 |" in text and "| 1/2 | 1 |" in text
    assert "应为「安装合同」，实际《虚构审批表.pdf》" in text
    assert f"{WRONG}，{SILENT}" in text

    # 下次运行合同号的来源改对了：对比里列出这一变化
    prev = find_previous(runs, "20260102-000000")
    fixed = json.loads(json.dumps(result, ensure_ascii=False))
    for r in fixed["results"]:
        if r["key"] == "contract_no":
            r["source_ok"] = True
    assert diff_runs(prev, [fixed]) == [
        {"case": "虚构案", "key": "contract_no", "before": f"{CORRECT}，{SOURCE_WRONG}", "after": CORRECT}
    ]
