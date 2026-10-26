"""案件 eval：用真实案卷跑系统，按律师诉状整理的标准字段逐项打分，并与上次运行对比。

真实案卷含当事人信息，只放在服务器的私有目录（如 /srv/eval-cases），绝不进仓库。

用法（在 backend/ 目录下；Docker 部署时见 deploy/README.md「案件 eval」）：
    python scripts/eval_cases.py /srv/eval-cases                 # 回放：有缓存用缓存，没有才调用接口
    python scripts/eval_cases.py /srv/eval-cases --strict        # 严格回放：不调用任何接口（改规则后用）
    python scripts/eval_cases.py /srv/eval-cases --mode live     # 全部重新调用 OCR / LLM（换模型、改提示词后用）
    python scripts/eval_cases.py /srv/eval-cases --case 安装案2   # 只跑某个案件（可重复）
    python scripts/eval_cases.py eval_fixtures --strict --baseline eval_fixtures/baseline.json
                                                                  # 虚构案卷集 + 基线门槛（CI 用，变差即失败）

目录结构：
    /srv/eval-cases/
      安装案2/
        materials/            上传给系统的材料（审批表、合同、验收报告…），文件名保持原样
        expected.json         标准字段（见下）
        cache/                录制的 OCR / LLM 返回（自动生成）
      _runs/20261015-0930/    每次运行的 report.md、results.json、各案件 complaint.txt / fields.json

expected.json：
    {
      "title": "安装案 2（AH2009976 安装）",
      "decisions": {"payable_ratio": "100%"},          断点时代替律师作答：{字段: 取值}
      "fields": {
        "total_amount": 1488996,                         数字 → 按金额比（到分）
        "contract_sign_date": "2020年7月30日",           完整日期 → 按日期比
        "plaintiff_name_final": "日立电梯（中国）有限公司湖南分公司",   其他 → 去空白、全半角后全等
        "jurisdiction_text": {"value": "邵东市人民法院", "match": "contains"},  包含即可
        "defendant_legal_rep": null,                     null → 应留空（如工商信息系统取不到）
        "interest_term": {"pending": "确认单（五）第 11 题"},  待律师确认：列出但不计分
        "contract_sign_date": {"value": "2020年7月30日", "source": "安装合同"},  选填 source：预期来源文件名（或其中一段）
        "payment_clause_location": {"value": "第5条", "review": "待核实"}  选填 review：审核页应标待核实
      }
    }

错误归因（#108，case_eval/attribution.py）：
    - 错误 / 缺失的字段在扫描件的 OCR 文本里找标准值（只用已录制的 OCR 结果）：引用页有 → 抽取错误；
      只在别的页有 → 其他页有标准值；全部页都识别过仍没有 → 疑似 OCR 错误；还有页没识别 → 可能未读到；
      值出自文字版材料 → 非 OCR 字段；规则拼出的措辞（管辖句等）→ 规则推导，不归因
    - 报告里分类计数，逐字段列出；与上次运行对比时归因变化也列出

来源与静默错误（#59）：
    - 填了 source 的字段，把系统值回原文重新锚定（与应用同一套逻辑），按实际命中的文件判断来源对不对，
      不看模型在出处里自称来自哪个文件；哪份材料都没命中也算来源错误
    - 静默错误：审核页显示「正常」，但值错、标准答案要求待核实（review），或填了 source 却回原文没命中
    - 两项单独计数，不影响「正确 / 错误 / 缺失 / 待定」与准确率；与上次运行对比时也列出它们的变化
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 app 之前设置：config 在导入时读取环境变量。
# 会话 checkpoint 与扫描件暂存放临时目录，不碰线上 data/；运行日志关闭（不混进线上统计）。
# 原告信息表仍读线上那份（BRANCH_INFO_PATH），与律师实际使用的一致。
_TMP = tempfile.mkdtemp(prefix="case-eval-")
os.environ["CHECKPOINT_DB_PATH"] = os.path.join(_TMP, "checkpoints.sqlite")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["RUN_LOG_ENABLED"] = "0"

from app import config  # noqa: E402
from app.agent.graph import reset_graph  # noqa: E402
from app.services import branch_registry  # noqa: E402
from scripts.case_eval import baseline  # noqa: E402
from scripts.case_eval.cache import MODES  # noqa: E402
from scripts.case_eval.report import accuracy, find_previous, write_report  # noqa: E402
from scripts.case_eval.runner import run_case  # noqa: E402


def _case_dirs(root: str, only: list[str]) -> list[str]:
    names = sorted(
        n for n in os.listdir(root)
        if not n.startswith(("_", ".")) and os.path.isfile(os.path.join(root, n, "expected.json"))
    )
    if only:
        unknown = [n for n in only if n not in names]
        if unknown:
            raise SystemExit(f"找不到案件：{'、'.join(unknown)}（需有 expected.json）")
        names = [n for n in names if n in only]
    if not names:
        raise SystemExit(f"{root} 下没有案件（每个案件目录需有 expected.json 和 materials/）")
    return [os.path.join(root, n) for n in names]


async def _run_all(dirs: list[str], mode: str, strict: bool) -> list[dict]:
    try:
        return await _run_cases(dirs, mode, strict)
    finally:
        await reset_graph()  # 关掉 checkpoint 的 SQLite 连接线程，否则进程结束不了


async def _run_cases(dirs: list[str], mode: str, strict: bool) -> list[dict]:
    cases = []
    for d in dirs:
        print(f"[eval] {os.path.basename(d)} …", flush=True)
        result = await run_case(d, mode=mode, strict=strict)
        s = result.get("summary") or {}
        print(
            f"[eval] {os.path.basename(d)}："
            + (f"出错 {result['error']}" if result.get("error") else f"准确率 {accuracy(s)}")
            + f"，{result['duration_s']} 秒",
            flush=True,
        )
        cases.append(result)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="用真实案卷跑系统并按标准字段打分")
    parser.add_argument("root", help="案卷根目录，如 /srv/eval-cases")
    parser.add_argument("--case", action="append", default=[], help="只跑这个案件（目录名，可重复）")
    parser.add_argument("--mode", choices=MODES, default="replay")
    parser.add_argument("--strict", action="store_true", help="严格回放：缓存未命中即报错，不调用接口")
    parser.add_argument("--out", help="报告目录，默认 <root>/_runs")
    parser.add_argument("--baseline", help="与这份基线比较，变差或缓存未命中时退出码 1（CI 用）")
    parser.add_argument("--update-baseline", action="store_true", help="把这次的结论写入 --baseline")
    args = parser.parse_args()
    if args.strict and args.mode != "replay":
        raise SystemExit("--strict 只用于 replay 模式")
    if args.update_baseline and not args.baseline:
        raise SystemExit("--update-baseline 需要同时给出 --baseline 路径")

    dirs = _case_dirs(args.root, args.case)
    # 案卷根目录自带原告信息表（虚构案卷集 eval_fixtures/branch_info.json）时用它，不读线上那份
    fixture_table = os.path.join(args.root, "branch_info.json")
    if os.path.isfile(fixture_table):
        branch_registry.BRANCH_INFO_PATH = fixture_table
    runs_dir = args.out or os.path.join(args.root, "_runs")
    run_name = datetime.now().strftime("%Y%m%d-%H%M%S")
    cases = asyncio.run(_run_all(dirs, args.mode, args.strict))
    meta = {
        "run": run_name,
        "mode": args.mode,
        "strict": args.strict,
        "llm_model": config.DEEPSEEK_MODEL,
        "llm_fallback_model": config.LLM_FALLBACK_MODEL,
        "ocr_model": config.QWEN_OCR_MODEL,
        "cases": [os.path.basename(d) for d in dirs],
    }
    prev = find_previous(runs_dir, run_name)
    gate_text, failed = "", False
    if args.baseline and args.update_baseline:
        baseline.save(args.baseline, cases)
        print(f"[eval] 已更新基线：{args.baseline}", flush=True)
    elif args.baseline:
        base = baseline.load(args.baseline)
        if base is None:
            raise SystemExit(f"找不到基线：{args.baseline}（第一次用 --update-baseline 生成）")
        gate = baseline.compare(base, cases, args.root)
        gate_text, failed = baseline.render(gate, args.baseline), gate.failed
        print(gate_text, flush=True)
    path = write_report(os.path.join(runs_dir, run_name), meta, cases, prev, gate_text)
    print(f"[eval] 报告：{path}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
