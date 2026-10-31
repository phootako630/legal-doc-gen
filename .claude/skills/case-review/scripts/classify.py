# 诉状差异 LLM 初分（#91）：律师诉状 vs 系统诉状的每处差异，让模型先归为抽取错误 / 写法不同 / 材料外事实，
# 给出理由和材料里的依据片段；依据片段回材料原文核对，找不到的标「依据未找到，需人工判断」。
# 结果只是建议，逐条由人确认或改判（SKILL.md 第 7 步）。
#
# 用法：python3 -I classify.py <律师诉状.txt> <系统 complaint.txt> <材料文本目录> <输出.md> [--fake 回答.json]
#   材料文本目录：inspect.py 输出的 out/text。律师诉状本身、以及文件名含「起诉状 / 仲裁申请书 / 诉状」的文书
#   不算材料（#151 评审）。扫描件的 OCR 文本另存为「<原文件名去扩展名>.ocr.txt」放进来；目录旁有
#   inspect.py 的 summary.md 时，会检查扫描件是否都补了 OCR，没补的在结果里提示、「材料外事实」降为「无法判断」。
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
_NOT_EVIDENCE_RE = re.compile(r"起诉状|仲裁申请书|诉状")
_SUMMARY_FILE_RE = re.compile(r"^## \d+\. (.+)$")
_WS_RE = re.compile(r"\s+")
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


def load_materials(folder: Path, lawyer: Path | None = None) -> dict[str, str]:
    """材料文本；律师诉状和其他律师文书不是证据，排除在外（否则律师稿独有的事实也能「在材料里找到」）。"""
    skip = lawyer.resolve() if lawyer else None
    return {
        p.name: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(folder.glob("*.txt"))
        if p.resolve() != skip and not _NOT_EVIDENCE_RE.search(p.name)
    }


def unread_scans(folder: Path) -> list[str]:
    """
    inspect.py 的 summary.md（材料目录的上一级）里标为扫描件 / 部分扫描、却还没有 OCR 文本的材料：
    全扫描件要有 <名>.txt 或 <名>.ocr.txt，部分扫描要有 <名>.ocr.txt（文本层缺了扫描页）。
    """
    summary = folder.parent / "summary.md"
    if not summary.exists():
        return []
    missing, name = [], None
    for line in summary.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _SUMMARY_FILE_RE.match(line)
        if m:
            name = m.group(1)
            continue
        if name and line.startswith("- PDF") and ("扫描件" in line or "部分扫描" in line):
            stem = Path(name).stem
            ocr = (folder / f"{stem}.ocr.txt").exists()
            if not (ocr or ("部分扫描" not in line and (folder / f"{stem}.txt").exists())):
                missing.append(name)
    return missing


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
    """
    依据片段回材料原文核对：只忽略空白和换行，标点、文字须与原文连续一致（#151 评审：改过标点的片段
    不算照抄）；找不到就不信这条依据。
    """
    item = dict(item)
    if item.get("category") not in CATEGORIES:
        item["category"] = "无法判断"
    ev = _WS_RE.sub("", item.get("evidence") or "")
    if item["category"] in ("抽取错误", "写法不同"):
        found = [name for name, text in materials.items() if ev and len(ev) >= 4 and ev in _WS_RE.sub("", text)]
        item["evidence_ok"] = bool(found)
        if found:
            item["file"] = found[0]
    else:
        item["evidence_ok"] = True
    return item


def render(rows: list[dict], items: dict[int, dict], unread: list[str] | None = None) -> str:
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
    if unread:
        out[2:2] = [
            f"> ⚠️ 材料不全：以下扫描件还没有 OCR 文本，其中的事实模型看不到，「材料外事实」一律降为「无法判断」，"
            f"请人工核对原件：{'、'.join(unread)}",
            "",
        ]
    return "\n".join(out)


def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    fake = argv[argv.index("--fake") + 1] if "--fake" in argv else None
    if fake:
        args.remove(fake)
    lawyer, system, folder, output = (Path(p) for p in args[:4])
    rows = diffs(lawyer.read_text(encoding="utf-8-sig", errors="replace"),
                 system.read_text(encoding="utf-8", errors="replace"))
    materials = load_materials(folder, lawyer)
    unread = unread_scans(folder)
    if fake:
        answer = json.loads(Path(fake).read_text(encoding="utf-8"))
    else:
        answer = ask_llm(PROMPT.format(blocks="\n\n".join(_block(d, materials) for d in rows))) if rows else {}
    items = {int(it.get("n", 0)): check_evidence(it, materials) for it in answer.get("items") or []}
    if unread:
        # 扫描件没读到文本时，「材料里找不到」不等于材料外事实
        for it in items.values():
            if it["category"] == "材料外事实":
                it["category"] = "无法判断"
                it["reason"] = f"{it.get('reason') or ''}（有扫描件未补 OCR，材料可能不全）"
    output.write_text(render(rows, items, unread), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main(sys.argv[1:])
