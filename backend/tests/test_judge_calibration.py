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
    pairs_from_results,
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


def test_kappa_undefined_when_single_category():
    # 两边都只用「一致」：κ 分母为 0、没有定义，不能显示成完美校准（#154 评审）
    pairs = [_p(i, "一致", "一致") for i in range(5)]
    assert agreement(pairs) == 1.0 and cohen_kappa(pairs) is None
    assert "| 全部 | 5 | 100% | — |" in render(pairs, "m")


def test_sheet_rows_shuffled_and_labels_mapped_by_id(tmp_path):
    from openpyxl import load_workbook

    pairs = [_p(i, "", "一致") for i in range(20)]
    path = str(tmp_path / "表.xlsx")
    write_sheet(pairs, path, seed=1)
    wb = load_workbook(path)
    ws = wb.active
    ids = [ws.cell(r, 1).value for r in range(2, 22)]
    assert sorted(ids, key=int) == [str(i) for i in range(20)] and ids != [str(i) for i in range(20)]
    write_sheet(pairs, str(tmp_path / "again.xlsx"), seed=1)
    assert [c.value for c in load_workbook(str(tmp_path / "again.xlsx")).active["A"][1:]] == ids
    # 人工结论按编号对回，不按行序
    row = ids.index("7") + 2
    ws.cell(row, 6, "不一致")
    wb.save(path)
    assert read_labels(path) == {"7": "不一致"}


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
    first = ws["A2"].value
    ws["F2"] = "不一致"
    wb.save(path)
    assert read_labels(path) == {first: "不一致"}
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


def test_pairs_from_results_takes_judge_fields_only():
    # 从 eval 结果导出真实配对：只取交给裁判的白名单字段，一边为空的不要，编号中性且不与已有编号重复
    cases = [
        {"case": "虚构案甲", "results": [
            {"key": "handover_text", "match": "judge", "expected": "已全部移交被告", "actual": "已移交物业"},
            {"key": "handover_text", "match": "judge", "expected": "已全部移交被告", "actual": None},
            {"key": "defendant_name", "match": "exact", "expected": "虚构公司", "actual": "虚构公司"},
        ]},
        {"case": "虚构案乙", "results": [
            {"key": "jurisdiction_text", "match": "judge", "expected": "向虚构区人民法院起诉", "actual": "  "},
            {"key": "interest_rate_basis", "match": "judge", "expected": "一年期 LPR", "actual": "LPR"},
        ]},
    ]
    pairs = pairs_from_results(cases, taken={"N001"})
    assert [(p["id"], p["case"], p["key"]) for p in pairs] == [
        ("N002", "虚构案甲", "handover_text"),
        ("N003", "虚构案乙", "interest_rate_basis"),
    ]
    assert pairs[0]["lawyer"] == "已全部移交被告" and pairs[0]["system"] == "已移交物业"
    assert all(p["origin"] == "真实" for p in pairs)


def test_export_command_merges_base(tmp_path):
    import subprocess
    import sys

    results = tmp_path / "results.json"
    results.write_text(json.dumps({"cases": [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交"},
    ]}]}, ensure_ascii=False), encoding="utf-8")
    base = tmp_path / "base.json"
    base.write_text(json.dumps([{"id": "N001", "case": "旧", "key": "handover_text",
                                 "lawyer": "甲", "system": "乙"}], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "pairs.json"
    subprocess.run([sys.executable, "scripts/judge_calibrate.py", "export", str(results), str(out),
                    "--base", str(base)], check=True, capture_output=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [p["id"] for p in data] == ["N001", "N002"]
    assert load_pairs(str(out))[1].lawyer == "已全部移交"


def test_pairs_from_results_keep_eval_criteria(tmp_path):
    # expected.json 覆盖了默认判断标准时，导出的配对沿用 eval 实际用的标准，标注表和校准提示词都一致（#180 评审）
    custom = "比较移交对象：移交物业与移交被告不同"
    cases = [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交物业", "actual": "已全部移交被告",
         "criteria": custom},
    ]}]
    pairs = pairs_from_results(cases)
    assert pairs[0]["criteria"] == custom
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    assert load_pairs(str(path))[0].criteria == custom


def test_pairs_from_results_skip_to_fill_placeholder():
    # 系统段落里还有【待补充】：裁判按缺失直接判，不交给律师标注（#180 评审）
    cases = [{"case": "虚构案", "results": [
        {"key": "jurisdiction_text", "match": "judge", "expected": "故原告向虚构区人民法院提起诉讼。",
         "actual": "故原告向【待补充】人民法院提起诉讼。"},
        {"key": "jurisdiction_text", "match": "judge", "expected": "故原告向虚构区人民法院提起诉讼。",
         "actual": "故原告向虚构区人民法院起诉。"},
    ]}]
    assert [p["system"] for p in pairs_from_results(cases)] == ["故原告向虚构区人民法院起诉。"]


def _export(tmp_path, cases, *extra):
    import subprocess
    import sys

    results = tmp_path / "results.json"
    results.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "pairs.json"
    proc = subprocess.run([sys.executable, "scripts/judge_calibrate.py", "export", str(results), str(out), *extra],
                          capture_output=True, text=True)
    return proc, out


def test_export_refuses_empty_result(tmp_path):
    # 没有可导出的配对：不写空文件（否则后面生成标注表会失败），给出中文说明（#180 评审）
    proc, out = _export(tmp_path, [{"case": "虚构案", "results": [
        {"key": "defendant_name", "match": "exact", "expected": "甲", "actual": "甲"},
    ]}])
    assert proc.returncode != 0 and "没有可导出的配对" in proc.stderr
    assert not out.exists()


def test_export_file_is_private_from_creation(tmp_path):
    # 输出文件建立时就是 0600；已有的宽权限文件也先收紧再写（#180 评审）
    import os
    import stat

    target = tmp_path / "pairs.json"
    target.write_text("旧内容", encoding="utf-8")
    os.chmod(target, 0o644)
    proc, out = _export(tmp_path, [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交"},
    ]}])
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert json.loads(out.read_text(encoding="utf-8"))[0]["lawyer"] == "已全部移交"
