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
