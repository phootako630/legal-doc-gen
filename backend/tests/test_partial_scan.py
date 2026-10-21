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


# ── #79 评审补充 ───────────────────────────────────────────────────────────────
_LONG = "电子版文字层页面，内容足够长，判定为已有文字的页。签订日期：2024年3月1日"


def test_approval_budget_shared_and_batched(monkeypatch):
    # 两份各 8 页的扫描审批表：整个案件合计只读 AGENT_MAX_APPROVAL_OCR_PAGES 页，且并发不超过上限
    fid = file_store.put(b"%PDF-fake")
    files = [
        {"filename": f"审批表{i}.pdf", "identified_type": "审批表", "is_scanned": True,
         "text": "", "pages": [], "page_count": 8, "file_id": fid}
        for i in (1, 2)
    ]
    inflight = peak = 0

    async def fake(_pdf, n):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0)
        inflight -= 1
        return f"第{n}页审批表内容，足够长的识别文本"

    ocr = AsyncMock(side_effect=fake)
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    asyncio.run(nodes_mod.approval_ocr_node({"files": files}))
    assert ocr.await_count == nodes_mod.AGENT_MAX_APPROVAL_OCR_PAGES
    assert peak <= nodes_mod.OCR_MAX_CONCURRENCY


def test_partially_scanned_approval_reads_only_scanned_pages(monkeypatch):
    # 1 页电子封面 + 2 页扫描页：text 不为空也要补读扫描页，封面保留
    fid = file_store.put(b"%PDF-fake")
    f = {"filename": "审批表.pdf", "identified_type": "审批表", "is_scanned": True,
         "text": _LONG, "pages": [{"page": 1, "text": _LONG}], "page_count": 3, "file_id": fid}
    ocr = AsyncMock(side_effect=lambda _pdf, n: f"扫描页{n}：未付款金额 45,180.00 元，足够长")
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    out = asyncio.run(nodes_mod.approval_ocr_node({"files": [f]}))
    assert sorted(c.args[1] for c in ocr.await_args_list) == [2, 3]
    assert [p["page"] for p in out["files"][0]["pages"]] == [1, 2, 3]


def test_early_stop_keeps_later_text_layer_pages(monkeypatch):
    # 第 1 批 OCR 就找到目标，第 10 页的文字层不能被丢掉（签约日期锚在这页）
    fid = file_store.put(b"%PDF-fake")
    f = {"page_count": 10, "file_id": fid, "pages": [{"page": 10, "text": _LONG}]}
    clause = "第七条 争议：提交某某仲裁委员会仲裁。"
    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(return_value=clause))
    pages, _budget, _got = asyncio.run(nodes_mod._ocr_for_targets(b"x", f, 50, {"dispute"}))
    assert pages[-1]["page"] == 10 and "签订日期" in pages[-1]["text"]
    assert max(p["page"] for p in pages if p["page"] < 10) <= 5  # 第 1 批即停


def test_partially_scanned_report_keeps_text_date_and_ocrs_scanned_pages(monkeypatch):
    from app.services.extraction import apply_acceptance_date

    fid = file_store.put(b"%PDF-fake")
    text_page = "监督检验报告 设备品种：曳引驱动乘客电梯 批 准：某某 日期：2025年6月23日 文字层页面"
    report = {"filename": "验收报告.pdf", "identified_type": "验收报告", "is_scanned": True,
              "text": text_page, "pages": [{"page": 1, "text": text_page}], "page_count": 2,
              "file_id": fid}
    # 抽取阶段：文字层的批准日期照常由代码取
    fields: dict = {}
    apply_acceptance_date(fields, [report])
    assert fields["acceptance_latest_date"]["value"].startswith("2025")
    # 补读阶段：只 OCR 第 2 页，汇总时文字页也参与（台数按首页数为 2）
    second = "监督检验报告 设备品种：曳引驱动乘客电梯 批 准：某某 日期：2025年6月20日"
    ocr = AsyncMock(return_value=second)
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    out = asyncio.run(nodes_mod.report_ocr_node({"extracted_fields": fields, "files": [report]}))
    assert [c.args[1] for c in ocr.await_args_list] == [2]
    assert out["extracted_fields"]["elevator_qty_by_acceptance"]["value"] == 2
    assert "6月23日" in out["extracted_fields"]["acceptance_latest_date"]["value"]
