# 扫描验收报告 OCR 后的字段提取：批准日期（取最晚）、安装地点（取第一份）。确定性代码，可单测。
#
# 律师规则：验收日期取检验报告的批准 / 盖章日期，多份取最晚一批（确认单第 10 题）。
# 扫描报告（常是手机拍照）没有文本层，按需 OCR 后用这里的规则取值，不再交给 LLM 猜。
from __future__ import annotations

import re

from app.services.validators import parse_date

# 「批  准：…  日期：2022年12月26日」；OCR 常把表格拆成「| 批准 | … | 日期：… |」
_APPROVED_DATE_RE = re.compile(
    r"批\s*准[\s\S]{0,40}?(\d{4})\s*[年\-./]\s*(\d{1,2})\s*[月\-./]\s*(\d{1,2})"
)
# 「| 安装地点 | 邵东市建设北路XX号1栋 | 使用登记证编号 | / |」或「安装地点：……」
_INSTALL_ADDRESS_RE = re.compile(r"安装地点[\s|｜:：]*([^|｜\n]{4,60}?)\s*(?:[|｜\n]|使用登记|$)")


# OCR 输出格式不固定：markdown 表格（含 **粗体**）或 HTML 表格。取值前统一成「标签 值」的纯文本
_TAG_RE = re.compile(r"<[^>]+>")
# 每份监督检验报告的首页才有「设备品种」栏（后续页没有）：按首页数报告份数
_FIRST_PAGE_RE = re.compile(r"设备品种")


def _plain(text: str) -> str:
    # 单元格内换行（<br>）直接拼接；其余标签当作单元格分隔符
    text = re.sub(r"<br\s*/?>", "", text or "", flags=re.I)
    text = _TAG_RE.sub(" | ", text).replace("**", "")
    return re.sub(r"[ \t]+", " ", text)


def count_report_first_pages(pages: list[dict]) -> int:
    """
    扫描报告按首页计数（每台电梯一份报告）：手机拍照常把报告编号裁掉，按编号去重会少数，
    首页的「设备品种」栏则每份都在。
    """
    return sum(1 for page in pages if _FIRST_PAGE_RE.search(_plain(page.get("text") or "")))


def latest_approved_date(pages: list[dict]) -> tuple[str, int | None] | None:
    """各页「批准」后的日期取最晚一个，返回 (「2022年12月26日」, 页码)；取不到返回 None。"""
    best: tuple[tuple[int, int, int], int | None] | None = None
    for page in pages:
        for m in _APPROVED_DATE_RE.finditer(_plain(page.get("text") or "")):
            ymd = parse_date(f"{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日")
            if ymd and (best is None or ymd > best[0]):
                best = (ymd, page.get("page"))
    if best is None:
        return None
    (y, mo, d), page_no = best
    return f"{y}年{mo}月{d}日", page_no


def first_install_address(pages: list[dict]) -> tuple[str, int | None] | None:
    """第一份报告的「安装地点」，返回 (地址, 页码)；取不到返回 None。"""
    for page in pages:
        m = _INSTALL_ADDRESS_RE.search(_plain(page.get("text") or ""))
        if m:
            addr = re.sub(r"\s+", "", m.group(1)).strip("：:")
            if addr and addr != "/":
                return addr, page.get("page")
    return None
