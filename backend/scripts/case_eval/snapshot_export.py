# 线上案件快照 → eval 案卷（#157 第 3 步）。快照的写法见 app/services/case_snapshot.py、call_recorder.py。
#
# 一个导出完的案件（律师导出过起诉状）变成和真实案卷集同样的目录：
#   materials/<文件名>     上传的原件（按 source_id 从快照库取回）
#   cache/llm|ocr/…       这个案件自己的模型调用返回（按运行清单取回；上传时的 OCR 不在清单里，按时间推断）
#   branch_info.json      导出起诉状时的原告信息表
#   expected.json         标准答案 = 律师最终确认的字段；律师改过的字段在 note 里写明系统交付值；
#                         decision_rounds（每轮断点回答）、material_types（律师改过的材料类型）、material_order（上传顺序）、
#                         internet_allowed、as_of（导出那天）一并写入，回放时按律师当时的条件跑
#   snapshot.json         溯源：run_id、代码版本、提示词哈希、模型、各项警告
# 目录名只用日期与 run_id 前 8 位，不含当事人名称。
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from app import config
from app.services import call_keys, call_recorder, case_snapshot
from app.services.ocr_engine import _render_page_b64
from scripts.case_eval.scoring import _blank, _contacts_text, _norm_text

_CST = timezone(timedelta(hours=8))  # 律所在中国大陆 / 香港，无夏令时


def completed_runs(snapshot_dir: str) -> list[str]:
    """律师导出过起诉状（有最终字段）的案件，按开始时间排序。"""
    config.SNAPSHOT_DIR = snapshot_dir
    folder = os.path.join(snapshot_dir, "cases")
    runs = []
    for run_id in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        case = case_snapshot.load(run_id)
        if case and case.get("final"):
            runs.append((case.get("started_at") or 0, run_id))
    return [r for _, r in sorted(runs)]


def _value(node: Any) -> Any:
    if isinstance(node, list):  # 联系人：按审核页的汇总写法
        return _contacts_text(node)
    return node.get("value") if isinstance(node, dict) else node


def _expected_fields(final: dict[str, Any], delivered: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """律师最终值 → expected.json 的 fields；返回 (fields, 律师改过的字段)。
    律师也留空的字段没有标准答案，不写；系统给了值、律师清空的，写成应为空（null）。"""
    delivered = delivered or {}
    fields, edited = {}, []
    for key, node in final.items():
        want, had = _value(node), _value(delivered.get(key))
        changed = key in delivered and _norm_text(str(want)) != _norm_text(str(had))
        if _blank(want):
            if had is None or _blank(had):
                continue
            want = None
        spec: dict[str, Any] = {"value": want}
        if changed:
            edited.append(key)
            spec["note"] = f"律师在审核页改过（系统交付值：{had}）"
        fields[key] = spec
    return fields, edited


def export_case(snapshot_dir: str, run_id: str, out_root: str) -> dict[str, Any]:
    """导出一个案件，返回摘要（案件目录、字段数、律师改过的字段、警告）。"""
    config.SNAPSHOT_DIR = snapshot_dir
    case = case_snapshot.load(run_id)
    if not case or not case.get("final"):
        raise ValueError(f"{run_id}：没有快照，或律师还没导出起诉状")
    started = datetime.fromtimestamp(case.get("started_at") or 0, _CST)
    name = f"{started:%Y%m%d}-{run_id[:8]}"
    case_dir = os.path.join(out_root, name)
    if os.path.exists(case_dir):
        raise ValueError(f"{case_dir} 已存在，不覆盖")
    warnings: list[str] = []
    os.makedirs(os.path.join(case_dir, "materials"), mode=0o700)  # 写完整棵树再统一收紧（_lock_down）

    # 原件
    contents: dict[str, bytes] = {}
    for f in case.get("files") or []:
        path = case_snapshot.material_path(f.get("source_id") or "")
        if not path or not os.path.exists(path):
            warnings.append(f"缺原件：{f.get('filename')}（上传时快照未开启，或原件已过保留期）")
            continue
        with open(path, "rb") as fh:
            contents[f["filename"]] = fh.read()
        shutil.copyfile(path, os.path.join(case_dir, "materials", f["filename"]))

    # 回放缓存：运行清单里的调用 + 上传时识别扫描件类型的那次 OCR（只读第 1 页）
    cache = os.path.join(case_dir, "cache")
    call_recorder.materialize(run_id, cache)
    for f in case.get("files") or []:
        data = contents.get(f["filename"])
        if not (data and f.get("is_scanned") and f["filename"].lower().endswith(".pdf")):
            continue
        img = _render_page_b64(data, 1)
        key = call_keys.ocr_key(img) if img else None
        target = os.path.join(cache, "ocr", f"{key}.txt") if key else None
        if not target or os.path.exists(target):
            continue
        text, how = call_recorder.resolve(run_id, "ocr", key)
        if text is None:
            continue  # 上传时没识别首页（文件名已认出类型），回放时也不会读
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        if how == "推断":
            warnings.append(f"《{f['filename']}》首页 OCR 有多份录制，按时间推断取了一份")

    # 当时的原告信息表
    if case.get("branch_table"):
        table = os.path.join(snapshot_dir, "branch", f"{case['branch_table']}.json")
        if os.path.exists(table):
            shutil.copyfile(table, os.path.join(case_dir, "branch_info.json"))
        else:
            warnings.append("缺当时的原告信息表（已过保留期），回放用全局那份")

    fields, edited = _expected_fields(case["final"], case.get("delivered"))
    types = {f["filename"]: f["identified_type"] for f in case.get("files") or []
             if f.get("detected_type") and f.get("identified_type") != f.get("detected_type")}
    generated = datetime.fromtimestamp(case.get("generated_at") or case.get("started_at") or 0, _CST)
    expected = {
        "title": f"线上案件 {started:%Y-%m-%d}（{run_id[:8]}，律师改过 {len(edited)} 个字段）",
        "as_of": f"{generated:%Y-%m-%d}",
        "internet_allowed": bool(case.get("internet_allowed")),
        # 律师每一轮断点的原样回答，按顺序回放（同一字段多轮回答不同也不会串轮）
        "decision_rounds": [r.get("answers") or {} for r in case.get("decisions") or []],
        # 律师上传的顺序：材料在提示词里按这个顺序排列
        "material_order": [f["filename"] for f in case.get("files") or []],
        "fields": fields,
    }
    if types:
        expected["material_types"] = types
    _write_json(os.path.join(case_dir, "expected.json"), expected)

    current = case_snapshot.prompts_digest()
    if case.get("prompts") and current and case["prompts"] != current:
        warnings.append("提示词模板已改过：严格回放会缓存未命中，看「当时的代码」需切到 code_version 再跑")
    _lock_down(case_dir)
    meta = {"run_id": run_id, "code_version": case.get("code_version"), "prompts": case.get("prompts"),
            "models": case.get("models"), "edited": edited, "warnings": warnings}
    _write_json(os.path.join(case_dir, "snapshot.json"), meta)
    return {"case": name, "fields": len(fields), "edited": edited, "warnings": warnings}


def _lock_down(root: str) -> None:
    """导出的案卷含案卷原文与原告信息表：整棵目录树目录 0700、文件 0600（#160 评审）。"""
    os.chmod(root, 0o700)
    for dirpath, dirs, files in os.walk(root):
        for d in dirs:
            os.chmod(os.path.join(dirpath, d), 0o700)
        for f in files:
            os.chmod(os.path.join(dirpath, f), 0o600)


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(path, 0o600)
