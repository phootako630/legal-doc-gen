# case-review 差异预分类脚本（#91）：用虚构样例跑通三类差异，依据片段回材料核对。不调用模型（--fake）。
import importlib.util
import json
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "case-review"
_EX = _SKILL / "examples" / "fictional"


def _module():
    spec = importlib.util.spec_from_file_location("classify", _SKILL / "scripts" / "classify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(tmp_path, answer: dict) -> str:
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.md"
    _module().main([str(_EX / "lawyer.txt"), str(_EX / "system.txt"), str(_EX / "materials"), str(out),
                    "--fake", str(fake)])
    return out.read_text(encoding="utf-8")


def test_three_categories_with_checked_evidence(tmp_path):
    answer = json.loads((_EX / "fake.json").read_text(encoding="utf-8"))
    text = _run(tmp_path, answer)
    assert "共 3 处差异：写法不同 1 处，抽取错误 1 处，材料外事实 1 处" in text
    assert "《虚构安装合同.txt》签订日期：2024年3月8日" in text
    assert "依据未找到" not in text
    assert "人工结论" in text  # 留给人工确认的列


def test_fabricated_evidence_flagged_for_human(tmp_path):
    # 模型编造的依据（材料里没有）不能当依据，标「依据未找到，需人工判断」
    answer = {"items": [
        {"n": 1, "category": "写法不同", "reason": "x", "evidence": "合同约定剩余合同款为十一万元"},
        {"n": 2, "category": "抽取错误", "reason": "x", "evidence": "签订日期：2024年3月8日"},
        {"n": 3, "category": "随便写的类别", "reason": "x", "evidence": ""},
    ]}
    text = _run(tmp_path, answer)
    rows = [line for line in text.splitlines() if line.startswith("| 1 |") or line.startswith("| 3 |")]
    assert "依据未找到，需人工判断" in rows[0]
    assert "无法判断" in rows[1]  # 类别不在四类里按无法判断


def test_missing_model_answer_defaults_to_unknown(tmp_path):
    text = _run(tmp_path, {"items": []})
    assert "无法判断 3 处" in text


def _skill_layout(tmp_path, summary: str | None = None):
    """按 SKILL.md 的真实布局：inspect.py 的 out/text 里同时有律师诉状和材料文本，旁边是 summary.md。"""
    out = tmp_path / "out"
    text = out / "text"
    text.mkdir(parents=True)
    lawyer = text / "起诉状-虚构置业.txt"
    lawyer.write_text((_EX / "lawyer.txt").read_text(encoding="utf-8"), encoding="utf-8")
    for f in (_EX / "materials").glob("*.txt"):
        (text / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    if summary is not None:
        (out / "summary.md").write_text(summary, encoding="utf-8")
    return lawyer, text


def _run_layout(tmp_path, lawyer, text, answer):
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    result = tmp_path / "out.md"
    _module().main([str(lawyer), str(_EX / "system.txt"), str(text), str(result), "--fake", str(fake)])
    return result.read_text(encoding="utf-8")


def test_lawyer_complaint_in_text_dir_is_not_evidence(tmp_path):
    # 律师诉状与材料在同一目录：律师稿独有的事实不能「在材料里找到」（#151 评审）
    lawyer, text = _skill_layout(tmp_path)
    mod = _module()
    assert set(mod.load_materials(text, lawyer)) == {"虚构安装合同.txt", "虚构审批表.txt"}
    answer = {"items": [{"n": 3, "category": "写法不同", "reason": "x",
                         "evidence": "2025年1月10日，被告签收了原告提交的银行保函。"}]}
    out = _run_layout(tmp_path, lawyer, text, answer)
    row = next(line for line in out.splitlines() if line.startswith("| 3 |"))
    assert "依据未找到，需人工判断" in row


def test_changed_punctuation_is_not_verbatim(tmp_path):
    # 依据只忽略空白：标点改过就不算照抄（#151 评审）
    mod = _module()
    materials = {"合同.txt": "甲方支付，乙方交付。"}
    ok = mod.check_evidence({"category": "写法不同", "evidence": "甲方支付，乙方 交付"}, materials)
    changed = mod.check_evidence({"category": "写法不同", "evidence": "甲方支付；乙方交付"}, materials)
    assert ok["evidence_ok"] is True and changed["evidence_ok"] is False


def test_unread_scans_downgrade_outside_facts(tmp_path):
    # 扫描合同还没补 OCR 文本：提示材料不全，「材料外事实」降为「无法判断」（#151 评审）
    summary = "# 案卷材料盘点\n\n## 1. 虚构扫描合同.pdf\n- PDF，12 页，扫描件\n\n## 2. 虚构审批表.pdf\n- PDF，1 页，可解析文本\n"
    lawyer, text = _skill_layout(tmp_path, summary)
    answer = json.loads((_EX / "fake.json").read_text(encoding="utf-8"))
    out = _run_layout(tmp_path, lawyer, text, answer)
    assert "材料不全" in out and "虚构扫描合同.pdf" in out
    assert "材料外事实" not in out.split("| # |")[1]
    # 补上 OCR 文本后不再提示
    (text / "虚构扫描合同.ocr.txt").write_text("OCR 文本", encoding="utf-8")
    out = _run_layout(tmp_path, lawyer, text, answer)
    assert "材料不全" not in out and "材料外事实" in out


def test_partially_scanned_needs_ocr_file(tmp_path):
    summary = "## 1. 虚构安装合同.pdf\n- PDF，5 页，部分扫描\n"
    lawyer, text = _skill_layout(tmp_path, summary)
    assert _module().unread_scans(text) == ["虚构安装合同.pdf"]  # 只有文本层的 .txt 不够
