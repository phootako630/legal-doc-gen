# 条款位置的确定性兜底（#142）：模型对同一份合同时而写「第十二条」、时而写「第12.1条」，起诉状引用的条款号
# 不能取决于这次怎么写。按需 OCR 补读完合同后，用代码对照条款原文所在页：
#   - 细化到子项：条款原文在页里前面紧跟子项编号（「12.1 所有因本合同……」），而位置只写到条（「第十二条」
#     或「第12条」），且编号对得上（十二 ↔ 12）→「第12.1条」；
#   - 中文数字条号：合同自己用阿拉伯数字编子项（行首「6.1」）或正文写「第6条」时，「第六条」→「第6条」。
# 编号对不上、找不到原文时不改；原值留在出处里。律师改过的不动。
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
_NOISE_RE = re.compile(r"[\s|*#>]|<[^>]+>")
_PROBE_LEN = 12  # 用条款原文开头这么多个字回页里定位


def _compact(text: str) -> str:
    return _NOISE_RE.sub("", text or "")


def _candidate_files(files: list[dict], src: str) -> list[dict]:
    m = _SRC_FILE_RE.search(src)
    named = [f for f in files if m and f.get("filename") == m.group(1)]
    return named or [f for f in files if f.get("identified_type") in ("合同", "战略合同")]


def _sub_number_before(files: list[dict], clause: str) -> str | None:
    """条款原文在页里前面紧跟的子项编号（「12.1」）；找不到返回 None。"""
    probe = _compact(clause)[:_PROBE_LEN]
    if len(probe) < 6:
        return None
    for f in files:
        for pg in f.get("pages") or [{"text": f.get("text") or ""}]:
            page = _compact(pg.get("text") or "")
            at = page.find(probe)
            if at < 0:
                continue
            m = re.search(r"(\d{1,2}[.．]\d{1,2})[、.．:：]?$", page[max(at - 10, 0) : at])
            if m:
                return m.group(1).replace("．", ".")
    return None


def _uses_arabic(files: list[dict], n: int) -> bool:
    """合同自己用阿拉伯数字写这一条：行首有「n.1」这样的子项，或正文写着「第n条」。"""
    sub = re.compile(rf"(?m)^\s*(?:\|\s*)?{n}[.．]\d")
    art = re.compile(rf"第\s*{n}\s*条")
    return any(sub.search(f.get("text") or "") or art.search(f.get("text") or "") for f in files)


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
        candidates = _candidate_files(files, str(text.get("src") or ""))
        sub = _sub_number_before(candidates, str(text["value"]))
        if sub and int(sub.split(".")[0]) == n:
            value, why = f"第{sub}条", f"{label}原文前的子项编号为「{sub}」"
        elif not m.group(1).isdigit() and _uses_arabic(candidates, n):
            value, why = f"第{n}条", "合同用阿拉伯数字编条款"
        else:
            continue
        if value == raw:
            continue
        loc["value"] = value
        loc["src"] = f"{loc.get('src') or ''}（原写作「{raw}」，{why}，按代码核对改写）"
        loc.pop("anchor", None)
