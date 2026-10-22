# 保密回归：用一组可识别的虚构取值跑完 analyze → resume → generate，运行日志里一个都不许出现
#
# run_log 的约定是「只记字段 key、数量、耗时、token，绝不记取值或原文」。这里守着这条约定：
# 以后谁在日志里加了字段取值，本测试就会失败（test_detector_catches_leak 证明检查本身有效）。
import asyncio
import json
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.routers.generate import GenerateRequest, generate
from app.services import run_log

# 虚构、在日志正常内容里不可能巧合出现的取值（公司名、金额、条款原文、地址、编号）
_SECRETS = {
    "plaintiff_name_final": "虚构甲乙丙电梯工程有限公司",
    "defendant_name": "虚构丁戊己置业发展有限公司",
    "defendant_address": "虚构省虚构市梧桐巷七十七号",
    "contract_no": "XG-HT-2099-0731",
    "payment_clause_text": "甲方应于验收合格后叁拾柒日内支付至合同价款的玖拾柒",
    "breach_clause_text": "逾期付款每日按欠款万分之柒点叁计付违约金",
    "dispute_clause_text": "提交虚构市梧桐区人民法院诉讼解决",
}
# 金额不勾稽，触发冲突断点；律师 resume 时改成能勾稽的总价
_AMOUNTS = {
    "total_amount": 7316529.43,
    "paid_amount": 5123456.71,
    "unpaid_amount": 2193072.19,
}
_RESUMED_TOTAL = 7316528.90  # = 已付 + 未付
_LAWYER_VALUE = "律师补录的虚构联系人辛壬癸"


def _leaks(raw: str) -> list[str]:
    values = [*_SECRETS.values(), _LAWYER_VALUE]
    values += [str(v) for v in (*_AMOUNTS.values(), _RESUMED_TOTAL)]
    return [v for v in values if v in raw]


def _use_log(monkeypatch, tmp_path):
    path = tmp_path / "run.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    return path


def test_full_flow_logs_no_field_values(monkeypatch, tmp_path):
    path = _use_log(monkeypatch, tmp_path)
    fields = {k: {"value": v, "src": "《审批表.pdf》"} for k, v in _SECRETS.items()}
    fields |= {k: {"value": v, "src": "《审批表.pdf》"} for k, v in _AMOUNTS.items()}
    ok = {"can_proceed": True, "missing": [], "notes": ""}
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[ok, fields, "说明\n---HIGHLIGHT---\n- 1"]),
    )
    text = "审批表\n" + "\n".join(
        str(v) for v in (*_SECRETS.values(), *_AMOUNTS.values())
    )

    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": text}], True))
    assert state.pending is not None and state.pending.kind == "conflict"
    resumed = asyncio.run(run_resume(state.run_id, {"total_amount": _RESUMED_TOTAL}))
    final = {k: dict(v) for k, v in resumed.extracted_fields.items()}
    final["contacts"] = {"value": _LAWYER_VALUE, "src": "律师填写"}
    out = asyncio.run(
        generate(GenerateRequest(validated_json=final, run_id=state.run_id))
    )
    assert _SECRETS["defendant_name"] in out.complaint_text  # 取值确实流经了全流程

    raw = path.read_text(encoding="utf-8")
    events = {json.loads(line)["event"] for line in raw.splitlines()}
    assert {"run_start", "node", "resume", "run_state", "lawyer_edits"} <= events
    assert _leaks(raw) == []


def test_detector_catches_leak(monkeypatch, tmp_path):
    # 故意把一个字段取值写进日志：检查必须发现它
    path = _use_log(monkeypatch, tmp_path)
    run_log.log_event("oops", value=_SECRETS["defendant_name"])
    assert _leaks(path.read_text(encoding="utf-8")) == [_SECRETS["defendant_name"]]
