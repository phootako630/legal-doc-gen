# 线上案件快照（#157）：律师处理的每个案件留一份快照，导出成 eval 案卷后可严格回放、记到 Langfuse 分析。
# 律师导出起诉状时提交的最终字段就是这个案件的标准答案；律师改过的字段就是系统出错的地方。
#
# 布局（SNAPSHOT_DIR/）：
#   materials/<sha256>          上传的原件（按内容去重：同一份文件只存一次）；上传时写
#   branch/<sha256>.json        导出起诉状时的原告信息表（同样按内容去重）
#   cases/<run_id>/case.json    案件快照：
#       分析开始时  files（文件名、律师确认的类型、系统识别的类型、原件 source_id …）、internet_allowed、
#                   code_version（部署的提交号）、prompts（提示词模板的哈希：模板变了，旧快照回放会缓存未命中）、模型
#       断点作答时  恢复成功后才追加这一轮回答（恢复失败、律师重试不会留下没被接受的回答）
#       导出起诉状时 delivered（交给律师审核的字段）、final（律师确认后的字段）、branch_table
#   LLM / OCR 调用的返回另由 call_recorder 录在 calls/。
#
# - 开关 SNAPSHOT_ENABLED；保留 SNAPSHOT_RETENTION_DAYS（默认 180 天，律所同意），超期由定时清理循环删除。
#   导出起诉状时会刷新这个案件引用的原件与原告信息表的修改时间，不会先于案件快照被清掉。
# - 内容是案卷原文：各级目录 0700、文件 0600。运行日志「不记原文」的规则不变，快照是另一份存储。
# - 写快照失败只记一条不含内容的警告，绝不影响律师在用的流程。
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from app import config
from app.services.snapshot_store import write_bytes, write_text

logger = logging.getLogger(__name__)
SOURCE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# 快照里每个材料记录的项：与回放有关的（文件名、律师确认的类型）和溯源用的
_FILE_KEYS = ("filename", "identified_type", "detected_type", "is_scanned", "page_count", "source_id")
_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def _root(*parts: str) -> str:
    return os.path.join(config.SNAPSHOT_DIR, *parts)


def _case_path(run_id: str) -> str | None:
    return _root("cases", run_id, "case.json") if _RUN_ID_RE.match(run_id or "") else None


def _warn(step: str, exc: Exception) -> None:
    logger.warning("案件快照失败（%s）：%s", step, type(exc).__name__)


def prompts_digest() -> str | None:
    """提示词模板目录的哈希：判断旧快照能不能用当前代码回放。"""
    try:
        h = hashlib.sha256()
        for path in sorted(_PROMPTS_DIR.rglob("*")):
            if path.is_file():
                h.update(str(path.relative_to(_PROMPTS_DIR)).encode("utf-8"))
                h.update(path.read_bytes())
        return h.hexdigest()[:16]
    except OSError:
        return None


_git_version: str | None = None


def code_version() -> str | None:
    """
    案件当时运行的代码版本：部署镜像里的 APP_VERSION（提交号）；本地开发没有时现取 git 提交号。
    规则、解析、渲染代码变了而提示词没变时，靠它解释严格回放的差异（#159 评审）。
    """
    global _git_version
    if config.APP_VERSION and config.APP_VERSION != "unknown":
        return config.APP_VERSION
    if _git_version is None:
        try:
            out = subprocess.run(["git", "-C", str(_PROMPTS_DIR.parent), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=2, check=True)
            _git_version = out.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            _git_version = "unknown"
    return _git_version


def put_material(content: bytes) -> str | None:
    """上传时存一份原件，返回 source_id（内容的 sha256）；未开启或失败时返回 None。"""
    if not config.SNAPSHOT_ENABLED:
        return None
    try:
        source_id = hashlib.sha256(content).hexdigest()
        path = _root("materials", source_id)
        if os.path.exists(path):
            os.utime(path)  # 再次上传刷新修改时间，按最近一次使用计保留期
        else:
            write_bytes(path, content)
        return source_id
    except Exception as exc:  # noqa: BLE001
        _warn("存原件", exc)
        return None


def material_path(source_id: str) -> str | None:
    return _root("materials", source_id) if SOURCE_ID_RE.match(source_id or "") else None


def load(run_id: str) -> dict[str, Any] | None:
    path = _case_path(run_id)
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(run_id: str, case: dict[str, Any]) -> None:
    path = _case_path(run_id)
    if path:
        write_text(path, json.dumps(case, ensure_ascii=False, indent=1))


def start(run_id: str, files: list[dict], internet_allowed: bool, owner: str | None) -> None:
    """分析开始：记下这个案件的材料与运行条件。"""
    if not config.SNAPSHOT_ENABLED:
        return
    try:
        _save(run_id, {
            "run_id": run_id,
            "owner": owner,
            "started_at": time.time(),
            "internet_allowed": internet_allowed,
            "code_version": code_version(),
            "prompts": prompts_digest(),
            "models": {"llm": config.DEEPSEEK_MODEL, "ocr": config.QWEN_OCR_MODEL},
            "files": [{k: f.get(k) for k in _FILE_KEYS} for f in files],
            "decisions": [],
            "delivered": None,
            "final": None,
            "branch_table": None,
            "generated_at": None,
        })
    except Exception as exc:  # noqa: BLE001
        _warn("开始", exc)


def add_decisions(run_id: str, decisions: dict) -> None:
    """律师在断点处的一轮回答（按顺序追加；回放时依次喂回给断点）。"""
    if not config.SNAPSHOT_ENABLED:
        return
    try:
        case = load(run_id)
        if case is None:
            return
        case["decisions"].append({"at": time.time(), "answers": decisions})
        _save(run_id, case)
    except Exception as exc:  # noqa: BLE001
        _warn("断点作答", exc)


def finish(run_id: str | None, delivered: dict | None, final: dict, user: str | None) -> None:
    """
    律师导出起诉状：记下交付字段、律师最终字段和当时的原告信息表。多次导出以最后一次为准。
    只更新这个账号自己发起的案件（别人的 run_id 不能改写别人的快照）。
    """
    if not config.SNAPSHOT_ENABLED or not run_id:
        return
    try:
        case = load(run_id)
        if case is None or case.get("owner") != user:
            return
        case.update(delivered=delivered, final=final, generated_at=time.time(),
                    branch_table=_snapshot_branch_table())
        _save(run_id, case)
        for f in case.get("files") or []:  # 引用的原件跟着案件快照续期
            path = material_path(f.get("source_id") or "")
            if path and os.path.exists(path):
                os.utime(path)
    except Exception as exc:  # noqa: BLE001
        _warn("导出", exc)


def _snapshot_branch_table() -> str | None:
    """当时的原告信息表按内容存一份，返回其 sha256；没有表时 None。"""
    try:
        with open(config.BRANCH_INFO_PATH, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    digest = hashlib.sha256(data).hexdigest()
    path = _root("branch", f"{digest}.json")
    if os.path.exists(path):
        os.utime(path)
    else:
        write_bytes(path, data)
    return digest


def purge_old(retention_days: float | None = None) -> int:
    """删除超过保留期的案件快照、原件与原告信息表，返回删除的个数。"""
    days = config.SNAPSHOT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = time.time() - days * 86400
    removed = 0
    cases = _root("cases")
    if os.path.isdir(cases):
        for run_id in os.listdir(cases):
            path = os.path.join(cases, run_id, "case.json")
            if not os.path.exists(path):
                path = os.path.join(cases, run_id)  # 写了一半没有 case.json：按目录时间清
            try:
                if os.path.getmtime(path) < cutoff:
                    shutil.rmtree(os.path.join(cases, run_id))
                    removed += 1
            except FileNotFoundError:
                continue
    for kind in ("materials", "branch"):
        folder = _root(kind)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
