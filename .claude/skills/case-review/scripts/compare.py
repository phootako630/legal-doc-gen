# 律师诉状 vs 系统生成诉状：按句对齐，列出不同之处（Markdown）。
# 用法：python3 -I compare.py <律师诉状.txt> <系统 complaint.txt> [输出.md]
# 只做文字层面的对齐，差异的性质（抽取错误 / 模板写法不同 / 材料里没有的事实）由人判断。
import difflib
import re
import sys
from pathlib import Path


def strip_marks(text: str) -> str:
    """去掉系统贴的标注，只比对取值本身：⚠️ 待核实：X → X，【高亮冲突：X】→ X。【待补充】保留。"""
    text = re.sub(r"⚠️\s*待核实：", "", text)
    return re.sub(r"【高亮冲突：(.*?)】", r"\1", text)


def sentences(text: str) -> list[str]:
    text = strip_marks(text.replace("\ufeff", ""))
    # 律师稿常有缩进空格（含全角空格），比对时忽略
    text = re.sub(r"[ \t\u3000]+", "", text)
    # 不按冒号切：「依据合同第X条约定：…」应算一句
    parts = re.split(r"(?<=[。；;])|\n+", text)
    return [p for p in (s.strip() for s in parts) if p]


def main() -> None:
    lawyer = sentences(
        Path(sys.argv[1]).read_text(encoding="utf-8-sig", errors="replace")
    )
    system = sentences(Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace"))
    out = [
        "# 律师诉状 vs 系统生成",
        "",
        f"律师稿 {len(lawyer)} 句，系统稿 {len(system)} 句。",
        "",
        "| # | 类型 | 律师稿 | 系统稿 |",
        "|---|---|---|---|",
    ]
    n = 0
    sm = difflib.SequenceMatcher(a=lawyer, b=system, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        left, right = lawyer[a1:a2], system[b1:b2]
        label = {"replace": "不同", "delete": "系统缺", "insert": "系统多"}[op]
        for i in range(max(len(left), len(right))):
            n += 1
            l_txt = left[i] if i < len(left) else ""
            r_txt = right[i] if i < len(right) else ""
            out.append(
                f"| {n} | {label} | {l_txt.replace('|', '｜')} | {r_txt.replace('|', '｜')} |"
            )
    same = sum(b.size for b in sm.get_matching_blocks())
    out.insert(3, f"完全相同 {same} 句，差异 {n} 处。")
    text = "\n".join(out)
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(text, encoding="utf-8")
        print(sys.argv[3])
    else:
        print(text)


if __name__ == "__main__":
    main()
