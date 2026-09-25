# 企业信息查询 stub + 名称规则：律师确认单第 16 题——被告是分公司时只起诉分公司，不再暂停补总公司全称。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze
from app.services.company_lookup import lookup_company


def test_lookup_company_stub_returns_none():
    # 预留：未接入查询 API，恒返回 None
    assert asyncio.run(lookup_company("91350100M000100Y43")) is None
    assert asyncio.run(lookup_company(None)) is None


_OK_CHECKLIST = {"can_proceed": True, "missing": [], "notes": ""}


def _run(monkeypatch, extracted):
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[_OK_CHECKLIST, extracted, "无\n---HIGHLIGHT---\n"]),
    )
    return asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))


def test_branch_defendant_does_not_pause(monkeypatch):
    extracted = {
        "defendant_name": {"value": "某地产有限公司上海分公司", "src": "《审批表》"},
    }
    state = _run(monkeypatch, extracted)
    assert state.pending is None
    assert (
        state.extracted_fields["defendant_name"]["value"] == "某地产有限公司上海分公司"
    )


def test_branch_plaintiff_skips_confirm(monkeypatch):
    extracted = {
        "contract_type": {"value": "安装合同", "src": "《审批表》"},
        "plaintiff_branch_raw": {
            "value": "集团/营销网络/江苏分公司",
            "src": "《审批表》",
        },
        "defendant_name": {"value": "南京某置业有限公司", "src": "《审批表》"},
    }
    state = _run(monkeypatch, extracted)
    assert state.pending is None
    # 原告全称由规则拼出：总公司全称 + 分公司
    name = state.extracted_fields["plaintiff_name_final"]["value"]
    assert name == "日立电梯（中国）有限公司江苏分公司"
