# agent 流程测试：analyze → (冲突) interrupt → resume → pending=null；缺材料照常分析、只提醒
#
# mock 掉 nodes.chat，不触网、不消耗配额。用 asyncio.run 驱动 async runner。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume

def _patch_chat(monkeypatch, side_effect):
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=side_effect))


def _conflict_fields():
    # 金额不勾稽：900000 ≠ 638667.2 + 256266.8 → 校验冲突
    return {
        "total_amount": {"value": 900000, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
    }


def test_conflict_triggers_interrupt_then_resume_clears(monkeypatch):
    _patch_chat(
        monkeypatch,
        [
            _conflict_fields(),  # 抽取
            "校验说明\n---HIGHLIGHT---\n- 项1",  # resume 后的 validate prose
        ],
    )

    # analyze：应在校验节点冲突处 interrupt
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is not None
    assert state.pending.kind == "conflict"
    assert "total_amount" in state.pending.field_keys
    # 断点处已带确定性校验结论
    assert any(v["key"] == "amount_reconcile" and v["is_conflict"] for v in state.validations)

    # resume：律师把总价改成能勾稽的值 → 冲突消除，pending 清空
    resumed = asyncio.run(run_resume(state.run_id, {"total_amount": 894934}))
    assert resumed.run_id == state.run_id
    assert resumed.pending is None
    assert not any(v["is_conflict"] for v in resumed.validations)
    assert resumed.validation_report == "校验说明"
    assert resumed.readiness > 0


def test_missing_materials_continues_and_reminds(monkeypatch):
    # 律师在上传页点了「材料不全，仍然分析」：不终止、不暂停，缺材料的提醒进校验面板（#70）；
    # 清点由代码按文件类型判定，不调 LLM（#69）：只有抽取与说明文本两次调用
    chat = AsyncMock(
        side_effect=[
            {"defendant_name": {"value": "某被告", "src": "《安装合同.pdf》"}},
            "说明\n---HIGHLIGHT---\n",
        ]
    )
    monkeypatch.setattr(nodes_mod, "chat", chat)
    files = [{"filename": "安装合同.pdf", "text": "x", "identified_type": "合同"}]
    state = asyncio.run(run_analyze(files, True))
    assert state.pending is None
    assert chat.await_count == 2
    check = next(v for v in state.validations if v["key"] == "materials")
    assert check["needs_review"] and not check["is_conflict"]
    assert check["message"] == "未上传审批表、验收报告，律师已选择按现有材料继续分析，相关字段留待补充。"


def test_complete_materials_pass_without_reminder(monkeypatch):
    _patch_chat(monkeypatch, [{}, "说明\n---HIGHLIGHT---\n"])
    files = [
        {"filename": "审批表.pdf", "text": "x", "identified_type": "审批表"},
        {"filename": "安装合同.pdf", "text": "x", "identified_type": "合同"},
        {"filename": "监检报告.pdf", "text": "x", "identified_type": "验收报告"},
    ]
    state = asyncio.run(run_analyze(files, True))
    check = next(v for v in state.validations if v["key"] == "materials")
    assert check["passed"] and not check["needs_review"]


def test_no_conflict_completes_without_interrupt(monkeypatch):
    consistent = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "plaintiff_name_final": {"value": "某电梯公司", "src": "《审批表》"},
        "defendant_name": {"value": "某被告", "src": "《审批表》"},
    }
    _patch_chat(
        monkeypatch,
        [consistent, "无冲突\n---HIGHLIGHT---\n"],
    )
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is None
    assert not any(v["is_conflict"] for v in state.validations)
    assert state.validation_report == "无冲突"


def test_resume_unknown_run_id_raises(monkeypatch):
    _patch_chat(monkeypatch, [])
    try:
        asyncio.run(run_resume("nonexistent-run-id", {}))
    except KeyError:
        return
    raise AssertionError("未知 run_id 应抛 KeyError")


def test_readiness_counts_template_fills():
    # 就绪度按模板实际填空统计：原告 / 被告的待补充项会拉低就绪度（原先固定 10 个字段，缺这些仍 100%）
    from app.agent.nodes import _readiness
    from app.services.complaint_renderer import fill_status

    fields = {
        "plaintiff_branch_raw": {"value": "集团/江苏分公司", "src": "《审批表》"},
        "contract_type": {"value": "安装合同", "src": "《审批表》"},
        "defendant_name": {"value": "某某置业有限公司", "src": "《审批表》"},
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
    }
    keys, missing = fill_status(fields)
    assert "contract_action" not in keys  # 措辞类不计入
    assert {"plaintiff_credit_code", "defendant_address", "defendant_legal_rep"} <= missing
    assert "unpaid_amount" not in missing
    score = _readiness(fields, [])
    assert 0 < score < 100
    assert score == round((len(keys) - len(missing)) / len(keys) * 100)
