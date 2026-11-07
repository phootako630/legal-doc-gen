# 线上调用录制（#157）：每次 LLM（DeepSeek 主模型）与 OCR 调用的返回存进快照库，导出 eval 案卷时
# 还原成回放缓存，严格回放读到的就是律师当时看到的结果。
#
# 布局（SNAPSHOT_DIR/calls/）：
#   llm/<请求键>/<返回指纹>.json   {"result": 返回}       请求键与 eval 回放缓存共用（call_keys）
#   ocr/<请求键>/<返回指纹>.txt    识别文本
#   runs/<run_id>.jsonl            这次运行依次用到的 {kind, key, variant, t}
# 同一请求模型可能给出不同返回（两个律师上传相同材料、同一案件重跑）：按返回指纹分别保存、互不覆盖，
# 每个案件按自己的清单取回自己那一份（#158 评审）。上传时的 OCR 还不属于任何运行，不进清单，
# 导出时取运行开始前最近录下的那一份（resolve）。
#
# - 开关 SNAPSHOT_ENABLED（默认关）；备用模型的返回、空的 OCR 结果不录（与 eval 录制一致）。
# - 内容是案卷原文：SNAPSHOT_DIR 下各级目录 0700、文件 0600；保留 SNAPSHOT_RETENTION_DAYS（默认 180 天，
#   律所同意），超期由服务的定时清理循环删除；同一返回再次出现会刷新修改时间。
# - 录制失败只记一条不含内容的警告，绝不影响主流程。
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from app import config
from app.services import run_log
from app.services.call_keys import llm_key, ocr_key

logger = logging.getLogger(__name__)
_EXT = {"llm": "json", "ocr": "txt"}
_lock = threading.Lock()  # OCR 在线程池里并发录制，清单追加要串行


def calls_dir() -> str:
    return os.path.join(config.SNAPSHOT_DIR, "calls")


def _ensure_dir(path: str) -> None:
    """从 SNAPSHOT_DIR 起逐级建目录，每一级都设成 0700（#158 评审：makedirs 的 mode 只管最后一级）。"""
    root = os.path.abspath(config.SNAPSHOT_DIR)
    parts = os.path.relpath(os.path.abspath(path), root).split(os.sep)
    current = root
    for part in [""] + [p for p in parts if p not in ("", ".")]:
        current = os.path.join(current, part) if part else current
        if not os.path.isdir(current):
            os.makedirs(current, mode=0o700, exist_ok=True)
        if os.stat(current).st_mode & 0o777 != 0o700:
            os.chmod(current, 0o700)


def _write(path: str, text: str) -> None:
    _ensure_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _variant(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _record(kind: str, key: str, text: str) -> None:
    try:
        variant = _variant(text)
        _write(os.path.join(calls_dir(), kind, key, f"{variant}.{_EXT[kind]}"), text)
        run_id = run_log.current_run_id()
        if run_id:
            line = json.dumps({"kind": kind, "key": key, "variant": variant, "t": time.time()})
            path = os.path.join(calls_dir(), "runs", f"{run_id}.jsonl")
            with _lock:
                _ensure_dir(os.path.dirname(path))
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                os.chmod(path, 0o600)
    except Exception as exc:  # noqa: BLE001 — 录制是尽力而为，不能影响律师在用的流程
        logger.warning("调用录制失败（%s）：%s", kind, type(exc).__name__)


def record_llm(messages: list[dict], json_mode: bool, result: Any) -> None:
    """DeepSeek 主模型的一次成功返回。messages 须是调用方传入的原样（补 JSON 系统指令之前）。"""
    if not config.SNAPSHOT_ENABLED:
        return
    try:
        key, text = llm_key(messages, json_mode), json.dumps({"result": result}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("调用录制失败（llm）：%s", type(exc).__name__)
        return
    _record("llm", key, text)


def record_ocr(img_b64: str, text: str) -> None:
    """一页 OCR 的识别文本；空串多半是接口异常，不录。"""
    if not config.SNAPSHOT_ENABLED or not text:
        return
    _record("ocr", ocr_key(img_b64), text)


# ── 导出时取回（#157 导出脚本用）─────────────────────────────────────────────


def run_calls(run_id: str) -> list[dict[str, Any]]:
    """这次运行按顺序用到的调用（清单）；没有清单返回空列表。"""
    path = os.path.join(calls_dir(), "runs", f"{run_id}.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def resolve(run_id: str, kind: str, key: str) -> tuple[str | None, str]:
    """
    这次运行里这个请求的返回 → (文本, 来源)。来源：
      「本次运行」——清单里记着这次用的是哪一份（同一运行里同一请求出现多次时取最后一次）；
      「唯一」——不在清单里（如上传时的 OCR），录制库里只有一份；
      「推断」——不在清单里、有多份：取运行开始前最近录下的一份，没有更早的就取最早的一份。
    录制库里没有这个请求时返回 (None, "缺失")。
    """
    folder = os.path.join(calls_dir(), kind, key)
    calls = run_calls(run_id)
    mine = [c["variant"] for c in calls if c["kind"] == kind and c["key"] == key]
    if mine:
        path = os.path.join(folder, f"{mine[-1]}.{_EXT[kind]}")
        if os.path.exists(path):
            return _read(path), "本次运行"
    if not os.path.isdir(folder):
        return None, "缺失"
    files = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith("." + _EXT[kind])]
    if not files:
        return None, "缺失"
    if len(files) == 1:
        return _read(files[0]), "唯一"
    start = min((c["t"] for c in calls), default=time.time())
    before = [f for f in files if os.path.getmtime(f) <= start]
    pick = max(before, key=os.path.getmtime) if before else min(files, key=os.path.getmtime)
    return _read(pick), "推断"


def materialize(run_id: str, dest: str) -> int:
    """把这次运行清单里的调用还原成 eval 回放缓存（dest/llm/<键>.json、dest/ocr/<键>.txt），返回写出的个数。
    不在清单里的请求（上传时的 OCR）由导出脚本按需用 resolve 补。"""
    written = 0
    for kind, key in dict.fromkeys((c["kind"], c["key"]) for c in run_calls(run_id)):
        text, _ = resolve(run_id, kind, key)
        if text is None:
            continue
        path = os.path.join(dest, kind, f"{key}.{_EXT[kind]}")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written += 1
    return written


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def purge_old(retention_days: float | None = None) -> int:
    """删除修改时间早于保留期的录制文件与运行清单（及随之变空的请求目录），返回删除的文件数。"""
    days = config.SNAPSHOT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = time.time() - days * 86400
    removed = 0
    root = calls_dir()
    if not os.path.isdir(root):
        return 0
    for dirpath, _dirs, names in os.walk(root, topdown=False):
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
        if dirpath != root and os.path.basename(os.path.dirname(dirpath)) in _EXT:
            try:
                os.rmdir(dirpath)  # 请求目录空了就删；不空时抛错，忽略
            except OSError:
                pass
    return removed
