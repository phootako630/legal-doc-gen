# 抽取后的律师规则把关 + 质保金断点（律师确认单第 5、8、9 题）
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.services.extraction import apply_rule_guards


def test_defendant_address_from_approval_form_dropped():
    fields = {
        "defendant_address": {
            "value": "南京市秦淮区某路",
            "src": "《诉讼审批表.pdf》买方单位地址",
        },
        "defendant_legal_rep": {"value": "某人", "src": "《诉讼审批表.pdf》法定代表人"},
    }
    apply_rule_guards(fields)
    assert fields["defendant_address"]["value"] is None
    assert "工商登记" in fields["defendant_address"]["src"]
    assert fields["defendant_legal_rep"]["value"] is None


def test_defendant_address_from_other_source_kept():
    fields = {
        "defendant_address": {"value": "南京市雨花台区某路", "src": "《验收报告.pdf》"}
    }
    apply_rule_guards(fields)
    assert fields["defendant_address"]["value"] == "南京市雨花台区某路"


def test_payment_summary_marked_ai():
    fields = {
        "payment_clause_summary": {
            "value": "安装完成后付60%",
            "src": "《合同.pdf》第二十八章",
        }
    }
    apply_rule_guards(fields)
    apply_rule_guards(fields)  # 幂等
    assert fields["payment_clause_summary"]["src"].count("AI 归纳，待核实") == 1


_OK_CHECKLIST = {"can_proceed": True, "missing": [], "notes": ""}


def test_retention_pauses_then_sets_ratio(monkeypatch):
    extracted = {
        "retention_ratio": {"value": "5%", "src": "《合同》"},
        "retention_clause_text": {
            "value": "质保期满一年支付2%，满二年支付3%",
            "src": "《合同》",
        },
        "retention_unpaid_amount": {"value": "0.00元", "src": "《审批表》"},
    }
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[_OK_CHECKLIST, extracted, "无\n---HIGHLIGHT---\n"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is not None
    assert state.pending.kind == "confirm"
    assert state.pending.field_keys == ["payable_ratio"]
    assert state.pending.options == ["100%", "97%", "95%"]
    assert "0.00元" in state.pending.question

    resumed = asyncio.run(run_resume(state.run_id, {"payable_ratio": "97%"}))
    assert resumed.pending is None
    assert resumed.extracted_fields["payable_ratio"]["value"] == "97%"


def test_no_retention_no_pause(monkeypatch):
    extracted = {"retention_ratio": {"value": "无", "src": "《合同》"}}
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[_OK_CHECKLIST, extracted, "无\n---HIGHLIGHT---\n"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is None
    assert state.extracted_fields["payable_ratio"]["value"] == "100%"
