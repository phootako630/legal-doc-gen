# 诉状差异 LLM 初分（#91）：律师诉状 vs 系统诉状的每处差异，让模型先归为抽取错误 / 写法不同 / 材料外事实，
# 给出理由和材料里的依据片段；依据片段回材料原文核对，找不到的标「依据未找到，需人工判断」。
# 结果只是建议，逐条由人确认或改判（SKILL.md 第 7 步）。
#
# 用法：python3 -I classify.py <律师诉状.txt> <系统 complaint.txt> <材料文本目录> <输出.md> [--fake 回答.json]
#   材料文本目录：inspect.py 输出的 out/text（扫描件没有文本层的，可把 OCR 文本另存为 .txt 放进来）
#   --fake：不调用模型，按文件里的回答输出（测试用）；不加则调用 DeepSeek（DEEPSEEK_API_KEY），会产生费用，
#   调用前先征得用户同意。只调用国内模型服务。
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

CATEGORIES = ("抽取错误", "写法不同", "材料外事实", "无法判断")
EVIDENCE_MISSING = "依据未找到，需人工判断"
_SNIPPET_LINES = 8  # 每处差异附给模型的材料片段行数
_NOISE_RE = re.compile(r"[\s|*#>，,。；;：:（）()「」“”\"'、]")

PROMPT = """你在帮开发人员核对电梯合同纠纷起诉状：左边是律师写的，右边是系统按模板生成的。
对下面每一处差异，判断属于哪一类（只能选一个）：
- 抽取错误：材料里有正确的信息，系统取错或漏取了；
- 写法不同：取值没错，只是模板句式、措辞或规则与律师写法不同；
- 材料外事实：律师用了材料里找不到的信息；
- 无法判断：信息不够，判断不了。
「抽取错误」「写法不同」须给出依据：从下面的材料片段里**逐字照抄**一段（不改字、不补字），作为判断依据；
「材料外事实」「无法判断」依据留空。只依据给出的材料，不要推测法律结论。

只输出 JSON：{{"items": [{{"n": 序号, "category": "类别", "reason": "一句话理由", "evidence": "原文片段或空", "file": "片段所在文件名或空"}}]}}

{blocks}"""


def _load_compare():
    spec = importlib.util.spec_from_file_location("compare", Path(__file__).with_name("compare.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _compact(text: str) -> str:
    return _NOISE_RE.sub("", text or "")


def diffs(lawyer_text: str, system_text: str) -> list[dict]:
    """与 compare.py 同一套切句和对齐：返回每处差异 {n, kind, lawyer, system}。"""
    import difflib

    compare = _load_compare()
    a, b = compare.sentences(lawyer_text), compare.sentences(system_text)
    out: list[dict] = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        left, right = a[a1:a2], b[b1:b2]
        kind = {"replace": "不同", "delete": "系统缺", "insert": "系统多"}[op]
        for i in range(max(len(left), len(right))):
            out.append({"n": len(out) + 1, "kind": kind,
                        "lawyer": left[i] if i < len(left) else "", "system": right[i] if i < len(right) else ""})
    return out


def load_materials(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted(folder.glob("*.txt"))}


def _bigrams(text: str) -> set[str]:
    t = _compact(text)
    return {t[i : i + 2] for i in range(len(t) - 1)}


def snippets(diff: dict, materials: dict[str, str]) -> list[tuple[str, str]]:
    """与差异两边字面重合最多的材料行（数字重合加权），供模型判断；不把整份合同发给模型。"""
    probe = _bigrams(diff["lawyer"] + diff["system"])
    numbers = set(re.findall(r"\d[\d,.]*\d|\d", diff["lawyer"] + diff["system"]))
    scored = []
    for name, text in materials.items():
        for line in text.splitlines():
            if len(line.strip()) < 4:
                continue
            score = len(probe & _bigrams(line)) + 5 * sum(1 for x in numbers if x in line)
            if score:
                scored.append((score, name, line.strip()[:200]))
    scored.sort(key=lambda s: -s[0])
    return [(name, line) for _, name, line in scored[:_SNIPPET_LINES]]


def _block(diff: dict, materials: dict[str, str]) -> str:
    lines = [f"### 差异 {diff['n']}（{diff['kind']}）", f"律师稿：{diff['lawyer'] or '（无）'}",
             f"系统稿：{diff['system'] or '（无）'}", "材料片段："]
    lines += [f"- 《{name}》{line}" for name, line in snippets(diff, materials)] or ["- （没有相关片段）"]
    return "\n".join(lines)


def ask_llm(prompt: str) -> dict:
    from openai import OpenAI  # 后端 requirements 已包含

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY 未设置：只调用国内模型服务，请先配置密钥")
    client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def check_evidence(item: dict, materials: dict[str, str]) -> dict:
    """依据片段回材料原文核对（去空白和标点后包含即可）；找不到就不信这条依据。"""
    item = dict(item)
    if item.get("category") not in CATEGORIES:
        item["category"] = "无法判断"
    ev = _compact(item.get("evidence") or "")
    if item["category"] in ("抽取错误", "写法不同"):
        found = [name for name, text in materials.items() if ev and len(ev) >= 4 and ev in _compact(text)]
        item["evidence_ok"] = bool(found)
        if found:
            item["file"] = found[0]
    else:
        item["evidence_ok"] = True
    return item


def render(rows: list[dict], items: dict[int, dict]) -> str:
    out = ["# 诉状差异 LLM 初分（仅供参考，请逐条确认或改判）", "",
           "| # | 类型 | 律师稿 | 系统稿 | LLM 初分 | 理由 | 依据 | 人工结论 |",
           "|---|---|---|---|---|---|---|---|"]
    counts: dict[str, int] = {}
    for d in rows:
        it = items.get(d["n"], {"category": "无法判断", "reason": "模型没有给出结论", "evidence_ok": True})
        counts[it["category"]] = counts.get(it["category"], 0) + 1
        if not it.get("evidence_ok", True):
            evidence = EVIDENCE_MISSING
        elif it.get("evidence"):
            evidence = f"《{it.get('file') or ''}》{it['evidence']}"
        else:
            evidence = ""
        cells = [str(d["n"]), d["kind"], d["lawyer"], d["system"], it["category"], it.get("reason") or "", evidence, ""]
        out.append("| " + " | ".join(c.replace("|", "｜").replace("\n", " ") for c in cells) + " |")
    summary = "，".join(f"{k} {v} 处" for k, v in counts.items())
    out[2:2] = [f"共 {len(rows)} 处差异：{summary}。LLM 分类只是建议，以人工结论为准。", ""]
    return "\n".join(out)


def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    fake = argv[argv.index("--fake") + 1] if "--fake" in argv else None
    if fake:
        args.remove(fake)
    lawyer, system, folder, output = (Path(p) for p in args[:4])
    rows = diffs(lawyer.read_text(encoding="utf-8-sig", errors="replace"),
                 system.read_text(encoding="utf-8", errors="replace"))
    materials = load_materials(folder)
    if fake:
        answer = json.loads(Path(fake).read_text(encoding="utf-8"))
    else:
        answer = ask_llm(PROMPT.format(blocks="\n\n".join(_block(d, materials) for d in rows))) if rows else {}
    items = {int(it.get("n", 0)): check_evidence(it, materials) for it in answer.get("items") or []}
    output.write_text(render(rows, items), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main(sys.argv[1:])
