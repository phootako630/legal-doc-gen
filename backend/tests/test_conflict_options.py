# 冲突断点候选值（#70）：各字段的现值、推算值与来源；数据均为虚构
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze
from app.services.conflict_options import conflict_options
from app.services.validators import run_all_checks


def _conflicts(fields):
    return [c for c in run_all_checks(fields) if c.is_conflict]


def _amounts(total, paid, unpaid):
    return {
        "total_amount": {"value": total, "src": "《审批表.pdf》合同总额"},
        "paid_amount": {"value": paid, "src": "《审批表.pdf》已付款"},
        "unpaid_amount": {"value": unpaid, "src": "《审批表.pdf》未付款"},
    }


def test_amount_conflict_lists_current_and_derived_values():
    fields = _amounts(900000, 638667.2, 256266.8)
    opts = conflict_options(_conflicts(fields), fields)
    assert opts["total_amount"] == [
        {"value": "900000", "source": "现值：《审批表.pdf》合同总额"},
        {"value": "894934", "source": "按已付 + 未付计算"},
    ]
    assert opts["paid_amount"][0]["value"] == "638667.2"  # 现值照原样
    assert opts["paid_amount"][1] == {
        "value": "643733.20",
        "source": "按总价 − 未付计算",
    }
    assert opts["unpaid_amount"][1] == {
        "value": "261332.80",
        "source": "按总价 − 已付计算",
    }


def test_current_amount_kept_as_written():
    # 现值照原样给出，与审核页输入框里的值一致；推算值写成可直接回填的数字
    fields = _amounts("894,934.00", 638667.2, 256366.8)
    opts = conflict_options(_conflicts(fields), fields)
    assert opts["total_amount"] == [
        {"value": "894,934.00", "source": "现值：《审批表.pdf》合同总额"},
        {"value": "895034", "source": "按已付 + 未付计算"},
    ]


def test_negative_derived_amount_is_not_offered():
    fields = _amounts(100000, 150000, 20000)
    opts = conflict_options(_conflicts(fields), fields)
    # 按总价 − 已付推算为负数（100000 − 150000），不作为候选
    assert [o["value"] for o in opts["unpaid_amount"]] == ["20000"]


def test_qty_conflict_lists_each_source_with_its_src():
    # 没有验收报告台数、审批表与合同不一致 → 冲突。候选带字段实际出处（#97 评审）
    fields = {
        "elevator_qty_by_approval": {"value": "6台", "src": "《审批表.pdf》第2页"},
        "elevator_qty_by_contract": {"value": 8, "src": "《安装合同（二）.pdf》第5页"},
    }
    opts = conflict_options(_conflicts(fields), fields)
    assert opts["elevator_qty"] == [
        {"value": "6", "source": "审批表写 6 台：《审批表.pdf》第2页"},
        {"value": "8", "source": "合同写 8 台：《安装合同（二）.pdf》第5页"},
    ]
    # 来源字段只给自身现值，不能用别的来源的台数补（#97 评审 P1）
    assert opts["elevator_qty_by_contract"] == [
        {"value": "8", "source": "现值：《安装合同（二）.pdf》第5页"}
    ]
    assert opts["elevator_qty_by_approval"] == [
        {"value": "6台", "source": "现值：《审批表.pdf》第2页"}
    ]
    assert "elevator_qty_by_acceptance" not in opts


def test_amount_with_chinese_unit_gets_no_derived_value():
    # 「万元」等单位 parse_amount 会剥掉不换算：含单位时只列现值，不给推算值（#97 评审）
    fields = _amounts("100万元", "60万元", "30万元")
    opts = conflict_options(_conflicts(fields), fields)
    assert opts["total_amount"] == [
        {"value": "100万元", "source": "现值：《审批表.pdf》合同总额"}
    ]
    assert len(opts["paid_amount"]) == 1 and len(opts["unpaid_amount"]) == 1
    # 带货币符号、千分位、「元」的纯数字写法照常推算
    fields = _amounts("¥900,000.00元", "638,667.20", 256266.8)
    opts = conflict_options(_conflicts(fields), fields)
    assert opts["total_amount"][1] == {"value": "894934", "source": "按已付 + 未付计算"}


def test_credit_code_conflict_offers_current_value_with_source():
    fields = {
        "defendant_credit_code": {
            "value": "91320000MA1XXXXXX0",
            "src": "《监检报告.pdf》",
        }
    }
    opts = conflict_options(_conflicts(fields), fields)
    assert opts == {
        "defendant_credit_code": [
            {"value": "91320000MA1XXXXXX0", "source": "现值：《监检报告.pdf》"}
        ]
    }


def test_conflict_pending_carries_field_options(monkeypatch):
    fields = _amounts(900000, 638667.2, 256266.8)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[fields]))
    state = asyncio.run(run_analyze([{"filename": "审批表.pdf", "text": "x"}], True))
    assert state.pending is not None and state.pending.kind == "conflict"
    options = state.pending.field_options
    assert set(options) == {"total_amount", "paid_amount", "unpaid_amount"}
    assert {"value": "894934", "source": "按已付 + 未付计算"} in options["total_amount"]
