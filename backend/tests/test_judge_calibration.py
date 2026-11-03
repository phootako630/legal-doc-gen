# 裁判校准（#90 第 3 步）：指标计算、标注表读写、配对校验、报告。全部虚构数据，不调用模型。
import asyncio
import json

import pytest

from scripts.case_eval.calibration import (
    Pair,
    agreement,
    cohen_kappa,
    confusion,
    judge_pairs,
    load_pairs,
    read_labels,
    render,
    write_sheet,
)
from scripts.case_eval.judge import BY_CODE


def _p(i, human, verdict, by="qwen", key="handover_text", origin="真实"):
    return Pair(id=str(i), case="虚构案", key=key, lawyer="甲", system="乙", origin=origin,
                human=human, verdict=verdict, by=by)


def test_agreement_and_kappa():
    pairs = [_p(1, "一致", "一致"), _p(2, "一致", "不一致"), _p(3, "不一致", "不一致"), _p(4, "不一致", "不一致")]
    assert agreement(pairs) == 0.75
    # 实际一致 0.75；碰巧一致 = 0.5×0.25 + 0.5×0.75 = 0.5 → κ = 0.25 / 0.5
    assert cohen_kappa(pairs) == pytest.approx(0.5)
    m = confusion(pairs)
    assert m["一致"]["不一致"] == 1 and m["不一致"]["不一致"] == 2


def test_kappa_penalizes_always_agree():
    # 9 对本来一致、1 对不一致，裁判一律说一致：一致率 90%，kappa 为 0（没有区分能力）
    pairs = [_p(i, "一致", "一致") for i in range(9)] + [_p(9, "不一致", "一致")]
    assert agreement(pairs) == 0.9
    assert cohen_kappa(pairs) == pytest.approx(0.0)


def test_unlabeled_or_unjudged_pairs_not_scored():
    pairs = [_p(1, "一致", "一致"), _p(2, "", "一致"), _p(3, "不一致", "未裁判")]
    assert agreement(pairs) == 1.0
    assert agreement([_p(1, "", "一致")]) is None and cohen_kappa([]) is None


def test_sheet_roundtrip_hides_verdict(tmp_path):
    pairs = [_p(1, "", "一致"), _p(2, "", "不一致")]
    pairs[0].reason = "裁判理由不该出现在表里"
    path = str(tmp_path / "表.xlsx")
    write_sheet(pairs, path)
    from openpyxl import load_workbook

    wb = load_workbook(path)
    ws = wb.active
    cells = [str(c.value or "") for row in ws.iter_rows() for c in row]
    assert not any("裁判理由" in c for c in cells) and "真实" not in cells
    ws["F2"] = "不一致"
    wb.save(path)
    assert read_labels(path) == {"1": "不一致"}
    ws["F3"] = "差不多"
    wb.save(path)
    with pytest.raises(ValueError, match="差不多"):
        read_labels(path)


def test_load_pairs_rejects_verbatim_field_and_duplicate_ids(tmp_path):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([{"id": "a", "key": "defendant_name", "lawyer": "x", "system": "y"}]), "utf-8")
    with pytest.raises(ValueError, match="白名单"):
        load_pairs(str(path))
    dup = [{"id": "a", "key": "handover_text", "lawyer": "x", "system": "y"}] * 2
    path.write_text(json.dumps(dup), "utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_pairs(str(path))
    path.write_text(json.dumps(dup[:1]), "utf-8")
    assert load_pairs(str(path))[0].criteria  # 默认判断标准


def test_judge_pairs_and_report():
    pairs = [
        Pair(id="1", case="c", key="handover_text", lawyer="已全部移交物业", system="已全部移交物业", criteria="x"),
        Pair(id="2", case="c", key="jurisdiction_text", lawyer="向甲市人民法院起诉", system="向乙市人民法院起诉",
             criteria="x", origin="构造"),
    ]

    async def ask(messages):
        return {"reason": "法院相同", "verdict": "一致"}

    asyncio.run(judge_pairs(pairs, ask, "fake-model"))
    assert pairs[0].by == BY_CODE and (pairs[1].verdict, pairs[1].by) == ("一致", "fake-model")
    pairs[0].human, pairs[1].human = "一致", "不一致"
    text = render(pairs, "fake-model")
    assert "「人工不一致、裁判一致」：1 对" in text and "| 构造反例 | 1 | 0% |" in text
    assert "向乙市人民法院起诉" in text  # 不同的配对逐条列出
