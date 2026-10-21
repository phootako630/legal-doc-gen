# 部分扫描的 PDF 与扫描审批表（律师反馈的山东裕景、徐州九里苏宁案）：
# 大部分页是扫描页 → 按扫描件按需 OCR，有文字层的页直接用；扫描审批表在清点前整份 OCR。
# OCR 与 LLM 一律打桩，材料为虚构数据。
import asyncio
from unittest.mock import AsyncMock

import fitz

from app.agent import nodes as nodes_mod
from app.services import file_parser, file_store

_TEXT_PAGE = "专用条件 第19条 争议解决：提交某某仲裁委员会仲裁。本页为电子版文字层，长度足够判定为文字页。"


def _pdf(page_texts: list[str]) -> bytes:
    doc = fitz.open()
    for t in page_texts:
        page = doc.new_page()
        if t:
            # 内置中文字体，保证文字层可被提取
            page.insert_text((50, 72), t, fontname="china-s", fontsize=9)
    return doc.tobytes()


def _parse(filename: str, content: bytes) -> dict:
    return asyncio.run(
        file_parser._parse_pdf(filename, content, file_parser._identify_type(filename))
    )


def test_mostly_scanned_pdf_is_treated_as_scanned_with_text_pages_kept():
    # 4 页里 3 页没有文字层：总字数超过阈值，但仍按扫描件处理
    out = _parse("某项目电梯安装合同.pdf", _pdf(["", "", "", _TEXT_PAGE + _TEXT_PAGE]))
    assert out["is_scanned"] is True
    assert out["file_id"]
    assert [p["page"] for p in out["pages"]] == [4]
    assert "第19条" in out["text"]
    assert out["identified_type"] == "合同"


def test_mostly_text_pdf_stays_text():
    # 只有签章页是扫描图：仍是文字版，不改变原有行为
    out = _parse("某项目电梯安装合同.pdf", _pdf([_TEXT_PAGE, _TEXT_PAGE, ""]))
    assert out["is_scanned"] is False
    assert "file_id" not in out or not out.get("file_id")


def test_ocr_reuses_text_layer_pages_without_budget():
    fid = file_store.put(b"%PDF-fake")
    f = {"page_count": 4, "file_id": fid, "pages": [{"page": 4, "text": _TEXT_PAGE}]}
    seen: list[int] = []

    async def fake(_pdf, n):
        seen.append(n)
        return "扫描页正文"

    nodes_mod_ocr = AsyncMock(side_effect=fake)
    orig = nodes_mod.ocr_page
    nodes_mod.ocr_page = nodes_mod_ocr
    try:
        pages, budget, got = asyncio.run(
            nodes_mod._ocr_for_targets(b"%PDF-fake", f, 10, {"dispute"})
        )
    finally:
        nodes_mod.ocr_page = orig
    assert sorted(seen) == [1, 2, 3]  # 第 4 页有文字层，不 OCR
    assert budget == 7
    assert got and [p["page"] for p in pages] == [1, 2, 3, 4]


def test_text_pages_still_read_after_budget_exhausted():
    # 预算 0：扫描页一页都不 OCR，但后面有文字层的页照样读到
    fid = file_store.put(b"%PDF-fake")
    f = {"page_count": 12, "file_id": fid, "pages": [{"page": 11, "text": _TEXT_PAGE}]}
    ocr = AsyncMock(return_value="不应调用")
    orig = nodes_mod.ocr_page
    nodes_mod.ocr_page = ocr
    try:
        pages, budget, got = asyncio.run(nodes_mod._ocr_for_targets(b"x", f, 0, {"dispute"}))
    finally:
        nodes_mod.ocr_page = orig
    ocr.assert_not_awaited()
    assert [p["page"] for p in pages] == [11] and budget == 0 and got


def test_scanned_approval_form_is_ocred_before_intake(monkeypatch):
    fid = file_store.put(b"%PDF-fake")
    files = [
        {"filename": "某某诉讼审批表.pdf", "identified_type": "审批表", "is_scanned": True,
         "text": "", "pages": [], "page_count": 2, "file_id": fid},
        {"filename": "安装合同.pdf", "identified_type": "合同", "is_scanned": True,
         "text": "", "pages": [], "page_count": 30, "file_id": fid},
    ]
    ocr = AsyncMock(side_effect=lambda _pdf, n: f"审批表第{n}页：未付款金额 45,180.00")
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    out = asyncio.run(nodes_mod.approval_ocr_node({"files": files}))
    approval, contract = out["files"]
    assert ocr.await_count == 2  # 只读审批表的 2 页，合同留给按需 OCR
    assert "未付款金额" in approval["text"] and len(approval["pages"]) == 2
    assert contract["text"] == "" and files[0]["text"] == ""  # 不改原状态


def test_approval_ocr_noop_for_text_forms(monkeypatch):
    ocr = AsyncMock()
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    files = [{"filename": "审批表.pdf", "identified_type": "审批表", "is_scanned": False,
              "text": "合同号 AH000", "pages": []}]
    assert asyncio.run(nodes_mod.approval_ocr_node({"files": files})) == {}
    ocr.assert_not_awaited()
