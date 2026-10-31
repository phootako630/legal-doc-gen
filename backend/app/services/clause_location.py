# 条款位置的确定性兜底（#142）：模型对同一份合同时而写「第十二条」、时而写「第12.1条」，起诉状引用的条款号
# 不能取决于这次怎么写。按需 OCR 补读完合同后，用代码对照条款原文所在页：
#   - 细化到子项：条款原文在页里前面紧跟子项编号（「12.1 所有因本合同……」），而位置只写到条（「第十二条」
#     或「第12条」），且编号对得上（十二 ↔ 12）→「第12.1条」；
#   - 中文数字条号：合同自己用阿拉伯数字编子项（行首「6.1」）或正文写「第6条」时，「第六条」→「第6条」。
# 只认条款原文出处那份文件（与那一页）；编号对不上、原文找不到或命中不止一处时不改；原值留在出处里。律师改过的不动。
from __future__ import annotations

import re

from app.services.derived_fields import _cn_int

# (位置字段, 条款原文字段, 中文名)
CLAUSE_PAIRS = (
    ("dispute_clause_location", "dispute_clause_text", "争议条款"),
    ("payment_clause_location", "payment_clause_text", "付款条款"),
)
_ARTICLE_RE = re.compile(r"^第\s*([一二三四五六七八九十两\d]{1,4})\s*条$")
_SRC_FILE_RE = re.compile(r"《([^》]+)》")
_SRC_PAGE_RE = re.compile(r"》第(\d+)页")
_NOISE_RE = re.compile(r"[\s|*#>]|<[^>]+>")
_PROBE_LEN = 30  # 用条款原文开头这么多个字回页里定位（不足则用全文），须唯一命中
_SUB_AT_LINE_START_RE = re.compile(r"(\d{1,2}[.．]\d{1,2})[、.．:：]?$")


def _compact(text: str) -> str:
    return _NOISE_RE.sub("", text or "")


def _source_pages(files: list[dict], src: str) -> list[dict] | None:
    """
    条款原文出处指向的那份文件（出处写了页码时只取那一页）；《文件名》对不上任何上传文件时返回 None，
    不拿别的合同当依据（#147 评审）。
    """
    m = _SRC_FILE_RE.search(src)
    f = next((f for f in files if m and f.get("filename") == m.group(1)), None)
    if f is None:
        return None
    pages = f.get("pages") or [{"page": None, "text": f.get("text") or ""}]
    page = _SRC_PAGE_RE.search(src)
    if page:
        pages = [p for p in pages if p.get("page") == int(page.group(1))] or pages
    return pages


def _sub_number_before(pages: list[dict], clause: str) -> str | None:
    """
    条款原文在页里前面紧跟、且位于行首的子项编号（「12.1」）。原文开头须在这些页里只命中一处，
    否则（如 12.1、12.2 开头相同）不判断，返回 None（#147 评审）。
    """
    probe = _compact(clause)[:_PROBE_LEN]
    if len(probe) < 6:
        return None
    hits: list[str | None] = []
    for pg in pages:
        lines = [_compact(line) for line in (pg.get("text") or "").splitlines()]
        flat, starts = "", set()
        for line in lines:
            starts.add(len(flat))
            flat += line
        at = flat.find(probe)
        while at >= 0:
            m = _SUB_AT_LINE_START_RE.search(flat[max(at - 10, 0) : at])
            begin = at - len(m.group(0)) if m else -1
            hits.append(m.group(1).replace("．", ".") if m and begin in starts else None)
            at = flat.find(probe, at + 1)
    return hits[0] if len(hits) == 1 else None


def _uses_arabic(pages: list[dict], n: int) -> bool:
    """同一份合同自己用阿拉伯数字写这一条：行首有「n.1」这样的子项，或正文写着「第n条」。"""
    sub = re.compile(rf"(?m)^\s*(?:\|\s*)?{n}[.．]\d")
    art = re.compile(rf"第\s*{n}\s*条")
    return any(sub.search(p.get("text") or "") or art.search(p.get("text") or "") for p in pages)


def refine_clause_locations(fields: dict, files: list[dict]) -> None:
    for loc_key, text_key, label in CLAUSE_PAIRS:
        loc = fields.get(loc_key)
        text = fields.get(text_key)
        if not (isinstance(loc, dict) and isinstance(text, dict) and text.get("value")):
            continue
        if str(loc.get("src") or "").startswith("律师"):
            continue
        raw = str(loc.get("value") or "").strip()
        m = _ARTICLE_RE.match(raw)
        n = _cn_int(m.group(1)) if m else None
        if not n:
            continue
        src = str(text.get("src") or "")
        pages = _source_pages(files, src)
        if pages is None:
            continue  # 出处对不上上传文件：不确定，不改
        sub = _sub_number_before(pages, str(text["value"]))
        whole = _source_pages(files, re.sub(r"》第\d+页", "》", src)) or pages
        if sub and int(sub.split(".")[0]) == n:
            value, why = f"第{sub}条", f"{label}原文前的子项编号为「{sub}」"
        elif not m.group(1).isdigit() and _uses_arabic(whole, n):
            value, why = f"第{n}条", "合同用阿拉伯数字编条款"
        else:
            continue
        if value == raw:
            continue
        loc["value"] = value
        loc["src"] = f"{loc.get('src') or ''}（原写作「{raw}」，{why}，按代码核对改写）"
        loc.pop("anchor", None)
