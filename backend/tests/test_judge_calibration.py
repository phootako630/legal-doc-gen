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


def _id_map(wb):
    return {str(no): pid for no, pid in wb["_编号"].iter_rows(min_row=2, values_only=True)}


def test_sheet_rows_shuffled_and_labels_mapped_by_id(tmp_path):
    from openpyxl import load_workbook

    pairs = [_p(i, "", "一致") for i in range(20)]
    path = str(tmp_path / "表.xlsx")
    write_sheet(pairs, path, seed=1)
    wb = load_workbook(path)
    ws = wb["标注"]
    # 第一栏只显示题号 1…20，内部编号在隐藏页里且顺序已打乱
    assert [ws.cell(r, 1).value for r in range(2, 22)] == list(range(1, 21))
    assert wb["_编号"].sheet_state == "veryHidden"
    order = [_id_map(wb)[str(i)] for i in range(1, 21)]
    assert sorted(order, key=int) == [str(i) for i in range(20)] and order != [str(i) for i in range(20)]
    write_sheet(pairs, str(tmp_path / "again.xlsx"), seed=1)
    again = _id_map(load_workbook(str(tmp_path / "again.xlsx")))
    assert [again[str(i)] for i in range(1, 21)] == order
    # 人工结论按题号还原成编号对回，不按行序
    row = order.index("7") + 2
    ws.cell(row, 6, "不一致")
    wb.save(path)
    assert read_labels(path) == {"7": "不一致"}


def test_sheet_does_not_show_internal_ids(tmp_path):
    # 真实配对 N001、构造配对 P01 前缀不同：标注页上看不到编号（#180 评审）
    from openpyxl import load_workbook

    pairs = [_p("N001", "", "一致"), _p("P01", "", "一致", origin="构造")]
    path = str(tmp_path / "表.xlsx")
    write_sheet(pairs, path)
    cells = [str(c.value or "") for row in load_workbook(path)["标注"].iter_rows() for c in row]
    assert not any(c in ("N001", "P01") for c in cells)


def test_old_sheet_without_id_page_still_readable(tmp_path):
    # 旧标注表（第一栏就是编号、没有隐藏页）照样能读
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "标注"
    ws.append(["编号", "字段", "判断标准", "律师句", "系统句", "你的结论", "备注"])
    ws.append(["P07", "handover_text", "x", "甲", "乙", "不一致", ""])
    path = str(tmp_path / "旧表.xlsx")
    wb.save(path)
    assert read_labels(path) == {"P07": "不一致"}


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
    ws = wb["标注"]
    cells = [str(c.value or "") for row in ws.iter_rows() for c in row]
    assert not any("裁判理由" in c for c in cells) and "真实" not in cells
    first = _id_map(wb)[str(ws["A2"].value)]
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
    pairs, dup = pairs_from_results(cases, base=[{"id": "N001", "case": "旧", "key": "handover_text",
                                                  "lawyer": "甲", "system": "乙"}])
    assert dup == 0
    assert [(p["id"], p["case"], p["key"]) for p in pairs] == [
        ("N002", "虚构案甲", "handover_text"),
        ("N003", "虚构案乙", "interest_rate_basis"),
    ]
    assert pairs[0]["lawyer"] == "已全部移交被告" and pairs[0]["system"] == "已移交物业"
    assert all(p["origin"] == "真实" for p in pairs)


def test_export_command_merges_base(tmp_path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps([{"id": "N001", "case": "旧", "key": "handover_text",
                                 "lawyer": "甲", "system": "乙"}], ensure_ascii=False), encoding="utf-8")
    proc, out = _export(tmp_path, [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交"},
    ]}], "--base", str(base))
    assert proc.returncode == 0, proc.stderr
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
    pairs, _ = pairs_from_results(cases)
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
    assert [p["system"] for p in pairs_from_results(cases)[0]] == ["故原告向虚构区人民法院起诉。"]


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


def test_duplicate_pairs_exported_once(tmp_path):
    # 同一份结果传两次、或把上次导出的文件当 --base 再导：同一（案件, 字段, 律师句, 系统句）只要一对（#180 评审）
    case = {"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交"},
    ]}
    pairs, dup = pairs_from_results([case, case])
    assert len(pairs) == 1 and dup == 1
    proc, out = _export(tmp_path, [case])
    assert proc.returncode == 0, proc.stderr
    again = tmp_path / "again"
    again.mkdir()
    proc, out2 = _export(again, [case], "--base", str(out))
    assert proc.returncode == 0 and "跳过重复 1 对" in proc.stdout
    assert len(json.loads(out2.read_text(encoding="utf-8"))) == 1


def test_default_criteria_not_frozen_into_pairs():
    # 用默认标准的字段不把标准写进配对：改了默认标准后重跑校准即用新标准（#180 评审）
    from scripts.case_eval.judge_rubrics import JUDGE_CRITERIA

    cases = [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交",
         "criteria": JUDGE_CRITERIA["handover_text"]},
    ]}]
    assert "criteria" not in pairs_from_results(cases)[0][0]


def test_export_rejects_invalid_base(tmp_path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps([{"id": "a", "key": "defendant_name", "lawyer": "x", "system": "y"}]), "utf-8")
    proc, out = _export(tmp_path, [{"case": "虚构案", "results": [
        {"key": "handover_text", "match": "judge", "expected": "已全部移交", "actual": "已移交"},
    ]}], "--base", str(base))
    assert proc.returncode != 0 and "--base" in proc.stderr and not out.exists()


def test_failed_write_keeps_existing_file(tmp_path):
    # 写入中途出错：原有文件（如律师已填的标注表）不动，也不留临时文件（#180 评审）
    import importlib.util
    import os
    import stat

    spec = importlib.util.spec_from_file_location("jc", "scripts/judge_calibrate.py")
    jc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jc)
    target = tmp_path / "标注表.xlsx"
    target.write_text("律师已填", encoding="utf-8")

    def boom(tmp):
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("写了一半")
        raise RuntimeError("写入失败")

    with pytest.raises(RuntimeError):
        jc._write_private(str(target), boom)
    assert target.read_text(encoding="utf-8") == "律师已填"
    assert os.listdir(tmp_path) == ["标注表.xlsx"]
    jc._write_text(str(target), "新内容")
    assert target.read_text(encoding="utf-8") == "新内容" and stat.S_IMODE(os.stat(target).st_mode) == 0o600
