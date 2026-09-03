# 出处定位：把抽取值回原文逐页锚定，算出「来自哪个文件的第几页」，供律师回溯。
#
# 设计意图（对应 CLAUDE.md「出处可追溯」）：
#   页码不信 LLM 自述，而是用确定性锚定（anchoring.anchor_value）逐页比对得出，
#   命中失败则页码为 None（配合 confidence 降权标「待核实」，不伪造页码）。
#   纯函数、可单测、不调用 LLM。
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.anchoring import AnchorQuality, anchor_value
from app.services.confidence import SourceChannel

# 从 src 文本里取出第一个《文件名》，用于优先在该来源文件内定位
_SRC_FILE_RE = re.compile(r"《([^》]+)》")

# 锚定质量排序：精确 > 模糊 > 未命中
_QUALITY_RANK: dict[str, int] = {"exact": 2, "fuzzy": 1, "none": 0}


@dataclass(frozen=True)
class Provenance:
    """单个字段值的出处定位结果。"""

    filename: str | None = None  # 命中所在文件名（None 表示未命中）
    page: int | None = None  # 命中所在页码（1 起；None 表示未命中或无分页信息）
    channel: SourceChannel = "text"  # 来源通道：命中于扫描件→ocr，否则 text
    anchor_quality: AnchorQuality = "none"
    matched_text: str | None = None  # 命中片段（用于前端高亮/反幻觉比对）


def _src_filename(src: str | None) -> str | None:
    """从 src（如「《安装合同.pdf》第3条」）里取出被引用的文件名。"""
    if not src:
        return None
    m = _SRC_FILE_RE.search(src)
    return m.group(1) if m else None


def _file_pages(file: dict) -> list[dict]:
    """取文件的逐页文本；无 pages 时退化为整份文本作单页（页码未知→page=None）。"""
    pages = file.get("pages") or []
    if pages:
        return pages
    text = file.get("text") or ""
    return [{"page": None, "text": text}] if text else []


def resolve_provenance(
    value: str | int | float | None,
    src: str | None,
    files: list[dict],
) -> Provenance:
    """
    在各文件逐页锚定 value，返回最佳命中的出处（文件名 + 页码 + 通道 + 质量）。

    命中排序：先按锚定质量（exact>fuzzy），同质量优先 src 引用的文件，
    再按文件出现顺序、页码升序。精确命中于优先文件时提前返回。
    value 为空或全部未命中 → Provenance()（page/filename 皆 None，quality=none）。
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return Provenance()

    preferred = _src_filename(src)
    # 把 src 引用的文件排到最前，其余保持原序
    ordered = sorted(
        files,
        key=lambda f: 0 if preferred and f.get("filename") == preferred else 1,
    )

    best: Provenance | None = None
    best_key: tuple[int, int] = (-1, -1)  # (quality_rank, is_preferred)

    for file in ordered:
        fname = file.get("filename")
        is_preferred = 1 if preferred and fname == preferred else 0
        channel: SourceChannel = "ocr" if file.get("is_scanned") else "text"
        for pg in _file_pages(file):
            result = anchor_value(value, pg.get("text", ""))
            if not result.hit:
                continue
            rank = _QUALITY_RANK[result.quality]
            key = (rank, is_preferred)
            if key > best_key:
                best_key = key
                best = Provenance(
                    filename=fname,
                    page=pg.get("page"),
                    channel=channel,
                    anchor_quality=result.quality,
                    matched_text=result.matched_text,
                )
                # 优先文件里的精确命中已是最优，无需再找
                if rank == _QUALITY_RANK["exact"] and is_preferred:
                    return best

    return best or Provenance()
