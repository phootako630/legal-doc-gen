# 出处定位：把抽取值回原文逐页锚定，算出「来自哪个文件的第几页」，供律师回溯。
#
# 设计意图（对应 AGENTS.md「出处可追溯」）：
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
# src 里模型写的页码「第5页」：值在多页都能命中时优先这一页，出处文字与按钮页码才一致（#120）。
# 只认《文件名》之外的：文件名里也可能有「第2页」（#122 评审）
SRC_PAGE_RE = re.compile(r"第\s*(\d+)\s*页")
# 按《…》切开 src：奇数段是文件名引用，偶数段是位置描述
_SRC_SPLIT_RE = re.compile(r"(《[^》]*》)")

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


def src_page(src: str | None) -> int | None:
    """src 里模型写的页码（「《合同.pdf》第5页盖章页」→ 5），不看文件名里的；没写返回 None。"""
    for i, part in enumerate(_SRC_SPLIT_RE.split(src or "")):
        if i % 2 == 0 and (m := SRC_PAGE_RE.search(part)):
            return int(m.group(1))
    return None


def replace_src_page(src: str, page: int) -> str:
    """把 src 位置描述里的第一个「第N页」改成 page，文件名引用原样保留。"""
    parts = _SRC_SPLIT_RE.split(src)
    for i in range(0, len(parts), 2):
        if SRC_PAGE_RE.search(parts[i]):
            parts[i] = SRC_PAGE_RE.sub(f"第{page}页", parts[i], count=1)
            break
    return "".join(parts)


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

    命中排序：先按锚定质量（exact>fuzzy），同质量优先 src 引用的文件，其中再优先 src 写的
    那一页（值在封面和盖章页都出现时，模型说第 5 页就取第 5 页，#120），
    再按文件出现顺序、页码升序。src 没写文件名时，只有值仅出现在一份文件里（文件能唯一确定）
    才按 src 写的页优先；否则「第 5 页」说不清是哪份文件的，不据此挑页。
    value 为空或全部未命中 → Provenance()（page/filename 皆 None，quality=none）。
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return Provenance()

    preferred = _src_filename(src)
    cited_page = src_page(src)
    # 把 src 引用的文件排到最前，其余保持原序
    ordered = sorted(
        files,
        key=lambda f: 0 if preferred and f.get("filename") == preferred else 1,
    )

    # 先收集全部命中（文件少、逐页比对便宜），再按排序键挑：需要知道值出现在几份文件里
    hits: list[tuple[int, int, Provenance]] = []  # (quality_rank, is_preferred, 出处)
    for file in ordered:
        fname = file.get("filename")
        is_preferred = 1 if preferred and fname == preferred else 0
        channel: SourceChannel = "ocr" if file.get("is_scanned") else "text"
        for pg in _file_pages(file):
            result = anchor_value(value, pg.get("text", ""))
            if not result.hit:
                continue
            hits.append((
                _QUALITY_RANK[result.quality],
                is_preferred,
                Provenance(
                    filename=fname,
                    page=pg.get("page"),
                    channel=channel,
                    anchor_quality=result.quality,
                    matched_text=result.matched_text,
                ),
            ))
    if not hits:
        return Provenance()

    single_file = len({h[2].filename for h in hits}) == 1

    def on_cited(is_preferred: int, prov: Provenance) -> int:
        if cited_page is None or prov.page != cited_page:
            return 0
        return 1 if is_preferred or (preferred is None and single_file) else 0

    # max 遇到并列取第一个：同等条件下仍按文件顺序、页码升序
    return max(hits, key=lambda h: (h[0], h[1], on_cited(h[1], h[2])))[2]
