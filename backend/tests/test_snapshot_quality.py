# 线上快照 → 抽取质量信号（#74）：改动分类、审核页状态还原、静默错误、断点候选选择、就绪度、报告不含取值。
# 全部虚构数据。
import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app import config
from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.routers.generate import GenerateRequest, generate
from app.services import case_snapshot, run_log
from scripts import snapshot_quality as cli
from scripts.case_eval import snapshot_quality as sq

SECRET_VALUES = ("虚构甲乙置业有限公司", "虚构甲乙置业发展有限公司", "HT-虚构-001", "张某某")


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(config, "BRANCH_INFO_PATH", str(tmp_path / "无.json"))
    return tmp_path / "snapshots"


def _save(case: dict) -> str:
    run_id = uuid.uuid4().hex
    case_snapshot._save(run_id, {"run_id": run_id, "started_at": 1_800_000_000, "files": [], **case})
    return run_id


def _node(value, src="《审批表.pdf》合同买方名称", channel="text", confidence=90):
    return {"value": value, "src": src, "channel": channel, "confidence": confidence}


DELIVERED = {
    "defendant_name": _node("虚构甲乙置业有限公司"),
    "total_amount": _node(1000.0),
    "unpaid_amount": _node(300.0),
    "elevator_qty": _node(3),
    "contract_no": _node("HT-虚构-001", src="《合同.pdf》第1页（OCR）", channel="ocr", confidence=50),
    "defendant_legal_rep": {"value": None, "src": ""},
    "contacts": [{"name": {"value": "虚构联系人"}, "phone": {"value": "13800000000"}}],
    # 审核页不显示的中间字段：律师导出时也会原样带回，不计入统计（#163 评审）
    "elevator_qty_by_approval": _node(3),
    "internet_lookup_status": _node("未查询"),
}
FINAL = {
    **DELIVERED,
    "defendant_name": {"value": "虚构甲乙置业发展有限公司", "src": "律师填写"},  # 标正常却被改 → 静默错误
    "total_amount": {"value": "1,000", "src": "律师填写"},  # 写法不同、数值相同 → 不算改
    "unpaid_amount": {"value": 400.0, "src": "律师填写"},  # 冲突字段被改 → 不算静默错误
    "contract_no": {"value": "", "src": ""},  # 待核实字段被清空
    "defendant_legal_rep": {"value": "张某某", "src": "律师填写"},  # 缺失字段被补填
    "elevator_qty_by_approval": {"value": 4, "src": "规则改写"},  # 审核页不显示：不算律师改动
}
REVIEW = {
    "validations": [
        {"key": "amount_consistency", "is_conflict": True, "related_fields": ["unpaid_amount"]},
        {"key": "qty_consistency", "is_conflict": True, "related_fields": []},
        {"key": "credit_code", "is_conflict": False, "related_fields": ["defendant_name"]},
    ],
    "readiness": 80,
}
DECISIONS = [
    {"answers": {"total_amount": 1000.0}, "pending": {
        "kind": "conflict", "field_keys": ["total_amount"],
        "field_options": {"total_amount": [{"value": "900", "source": "甲"}, {"value": "1000", "source": "乙"}]}}},
    {"answers": {"unpaid_amount": 123}, "pending": {
        "kind": "conflict", "field_keys": ["unpaid_amount"],
        "field_options": {"unpaid_amount": [{"value": "300", "source": "甲"}, {"value": "400", "source": "乙"}]}}},
]


def test_edit_kind():
    assert sq.edit_kind(None, "") is None
    assert sq.edit_kind(None, "甲") == "补填"
    assert sq.edit_kind("甲", " ") == "清空"
    assert sq.edit_kind("甲", "乙") == "修改"
    assert sq.edit_kind(1000.0, "1,000") is None and sq.edit_kind("ＡＢ １", "AB1") is None


def test_review_status_matches_review_page():
    conflicts = sq.conflict_keys(REVIEW["validations"])
    assert conflicts == {"unpaid_amount", "elevator_qty"}  # 台数一致性对应展示字段 elevator_qty
    status = {k: sq.review_status(v, conflicts, k) for k, v in DELIVERED.items() if k in sq.REVIEW_FIELD_KEYS}
    assert status == {
        "defendant_name": "normal", "total_amount": "normal", "unpaid_amount": "conflict",
        "elevator_qty": "conflict", "contract_no": "ocr_uncertain", "defendant_legal_rep": "missing",
        "contacts": "normal",
    }
    assert sq.review_status({"value": "x", "src": "金额不一致"}, set(), "k") == "conflict"


def test_summary_and_report(snap):
    _save({"delivered": DELIVERED, "final": FINAL, "delivered_review": REVIEW, "decisions": DECISIONS})
    # 旧快照：没记校验结论与候选项（#162 之前）
    _save({"delivered": DELIVERED, "final": FINAL, "decisions": [{"answers": {"total_amount": 1000.0}}]})
    _save({"delivered": DELIVERED, "final": None})  # 律师还没导出起诉状：不统计

    s = sq.summarize(sq.load_cases(str(snap)))
    assert s["cases"] == 2 and s["with_review"] == 1
    assert s["edits_per_case"] == [4, 4]
    assert dict(s["per_key"]["defendant_name"]) == {"cases": 2, "修改": 2}
    assert s["per_key"]["total_amount"]["修改"] == 0
    assert "elevator_qty_by_approval" not in s["per_key"] and "internet_lookup_status" not in s["per_key"]
    assert s["silent"] == {"defendant_name": 1}  # 旧快照还原不出状态，不计入
    assert dict(s["by_status"]["conflict"]) == {"n": 2, "edited": 1}
    assert dict(s["by_status"]["missing"]) == {"n": 1, "edited": 1}
    # 交付有值的字段：修改 / 清空才算改；通道与 confidence 分段
    assert dict(s["by_channel"]["OCR"]) == {"n": 2, "edited": 2}
    assert dict(s["by_conf"]["0–59"]) == {"n": 2, "edited": 2}
    assert dict(s["by_conf"]["80–100"]) == {"n": 8, "edited": 4}
    assert dict(s["by_conf"]["无"]) == {"n": 2, "edited": 0}  # 联系人没有 confidence
    assert s["choices"][("conflict", "total_amount")] == {"候选 2": 1}
    assert s["choices"][("conflict", "unpaid_amount")] == {"自填": 1}
    assert s["old_rounds"] == 1
    assert s["delivered_ready"] == [80] and len(s["final_ready"]) == 2

    report = sq.render(s)
    assert "| `defendant_name` | 2 | 0 | 0 | 2 | 100% |" in report
    assert "| `defendant_name` | 1 |" in report  # 静默错误
    assert "另有 1 个旧快照没记校验结论" in report and "另有 1 轮作答来自旧快照" in report
    for value in SECRET_VALUES:
        assert value not in report  # 报告只有字段 key 与计数


def test_same_choice_hint(snap):
    rnd = {"answers": {"total_amount": 900}, "pending": {"kind": "conflict", "options": ["900", "1000"]}}
    for _ in range(3):
        _save({"delivered": DELIVERED, "final": DELIVERED, "delivered_review": REVIEW, "decisions": [rnd]})
    report = sq.render(sq.summarize(sq.load_cases(str(snap))))
    assert "| conflict | `total_amount` | 3 | 候选 1 3 | 几乎总选同一个，可考虑改成规则 |" in report
    assert "（律师没改过任何字段）" in report


def test_empty(snap):
    assert "还没有律师导出过起诉状的案件" in sq.render(sq.summarize(sq.load_cases(str(snap))))


def test_cli_since_and_out(snap, tmp_path, monkeypatch, capsys):
    _save({"delivered": DELIVERED, "final": FINAL, "delivered_review": REVIEW, "started_at": 1_700_000_000})
    _save({"delivered": DELIVERED, "final": FINAL, "delivered_review": REVIEW, "started_at": 1_900_000_000})
    out = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["x", str(snap), "--since", "2027-01-01", "--out", str(out)])
    cli.main()
    assert "案件：1 个" in capsys.readouterr().out
    assert "案件：1 个" in out.read_text(encoding="utf-8")


def test_end_to_end_from_live_flow(snap, monkeypatch):
    """线上流程写出的快照（#162 起记了校验结论与断点候选）直接能统计：律师选了冲突断点的候选、改了被告名称。"""
    fields = {
        "defendant_name": {"value": "虚构甲乙置业有限公司", "src": "《审批表.pdf》"},
        "total_amount": {"value": 1000.0, "src": "《审批表.pdf》"},
        "paid_amount": {"value": 600.0, "src": "《审批表.pdf》"},
        "unpaid_amount": {"value": 300.0, "src": "《审批表.pdf》"},  # 不勾稽 → 冲突断点
    }
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[fields, "说明\n---HIGHLIGHT---\n- 1"]))

    async def go():
        run_log.set_user("lawyer-a")
        files = [{"filename": "审批表.pdf", "text": "审批表 虚构甲乙置业有限公司 1000 600 300"}]
        state = await run_analyze(files, True, "lawyer-a")
        resumed = await run_resume(state.run_id, {"total_amount": 900.0}, "lawyer-a")
        final = {k: dict(v) for k, v in resumed.extracted_fields.items()}
        final["defendant_name"] = {"value": "虚构甲乙置业发展有限公司", "src": "律师填写"}
        await generate(GenerateRequest(validated_json=final, run_id=state.run_id))

    asyncio.run(go())
    s = sq.summarize(sq.load_cases(str(snap)))
    assert s["cases"] == 1 and s["with_review"] == 1
    assert s["per_key"]["defendant_name"]["修改"] == 1
    picked = s["choices"][("conflict", "total_amount")]
    assert sum(picked.values()) == 1 and sq.CUSTOM not in picked  # 900 是断点给出的候选（另两项推出的值）
    assert s["delivered_ready"] and s["final_ready"]
    report = sq.render(s)
    for value in SECRET_VALUES:
        assert value not in report


def test_final_readiness_counts_unresolved_conflicts():
    """律师停在断点直接导出、金额仍不勾稽：重新校验仍是冲突，不能显示成就绪（#163 评审）。"""
    base = {
        "total_amount": {"value": 1000.0, "src": "x"},
        "paid_amount": {"value": 600.0, "src": "x"},
        "unpaid_amount": {"value": 300.0, "src": "x"},
    }
    unresolved = sq.final_readiness(base)
    resolved = sq.final_readiness({**base, "unpaid_amount": {"value": 400.0, "src": "律师填写"}})
    assert unresolved is not None and resolved is not None and unresolved < resolved
