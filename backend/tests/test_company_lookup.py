# 第7步单测：company_lookup 预留 stub + 名称待核实检测 + HITL confirm 断点往返。
#
# lookup_company 未接入 API 恒 None；分公司名称触发 confirm interrupt，resume 后回填法人全称。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.services.company_lookup import lookup_company


def test_lookup_company_stub_returns_none():
    # 预留：未接入查询 API，恒返回 None（→ 交由 HITL）
    assert asyncio.run(lookup_company("91350100M000100Y43")) is None
    assert asyncio.run(lookup_company(None)) is None


def test_names_needing_confirmation_detects_branch():
    fields = {
        "plaintiff_name_final": {"value": "某某电梯有限公司北京分公司", "src": "x"},
        "plaintiff_credit_code": {"value": "91350100M000100Y43", "src": "x"},
        "defendant_name": {"value": "某科技有限公司", "src": "x"},  # 非分公司
    }
    targets = nodes_mod._names_needing_confirmation(fields)
    keys = [t[0] for t in targets]
    assert "plaintiff_name_final" in keys
    assert "defendant_name" not in keys
    # 携带对应信用代码
    assert targets[0][2] == "91350100M000100Y43"


_OK_CHECKLIST = {"can_proceed": True, "missing": [], "notes": ""}


def test_branch_name_triggers_confirm_then_resume(monkeypatch):
    extracted = {
        "plaintiff_name_final": {
            "value": "某某电梯有限公司上海分公司",
            "src": "《审批表》",
        },
        "plaintiff_credit_code": {"value": "91350100M000100Y43", "src": "《审批表》"},
        "defendant_name": {"value": "某地产公司", "src": "《审批表》"},
    }
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(
            side_effect=[_OK_CHECKLIST, extracted, "校验说明\n---HIGHLIGHT---\n"]
        ),
    )

    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    # 疑为分公司 → confirm 断点
    assert state.pending is not None
    assert state.pending.kind == "confirm"
    assert "plaintiff_name_final" in state.pending.field_keys
    assert "企查查" in state.pending.question or "公示系统" in state.pending.question

    # 律师查询后键入法人全称 → resume，回填、清空断点
    resumed = asyncio.run(
        run_resume(state.run_id, {"plaintiff_name_final": "某某电梯有限公司"})
    )
    assert resumed.pending is None
    name = resumed.extracted_fields["plaintiff_name_final"]
    assert name["value"] == "某某电梯有限公司"
    assert name["src"] == "律师查询确认"


def test_no_branch_name_skips_confirm(monkeypatch):
    extracted = {
        "plaintiff_name_final": {"value": "某某电梯有限公司", "src": "《审批表》"},
        "defendant_name": {"value": "某地产公司", "src": "《审批表》"},
    }
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[_OK_CHECKLIST, extracted, "无\n---HIGHLIGHT---\n"]),
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is None
