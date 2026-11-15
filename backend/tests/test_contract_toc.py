# 长扫描合同按目录定位读页（#12）：目录解析为纯函数单测；按需 OCR 节点的读页顺序用假 ocr_page 验证。
# 全部虚构数据。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store
from app.services.contract_toc import toc_priority_pages

# 「页数」目录：数字是各部分的页数（真实合同模板的写法：表头「页数」，各部分另有一页分隔页）
_COUNT_TOC = """合同目录

| 序号 | 文件内容 | 页数 |
|------|----------|------|
| 1 | 本合同书 | 4 |
| 2 | 相关来往信函 | 59 |
| 3 | 分包合同专用条件及其附件 | 23 |
| 4 | 分包合同通用条件 | 1 |
| 5 | 技术规范/标准 | 59 |
| 6 | 合同图纸 | 17 |"""

# 「页码」目录：数字是起始页
_START_TOC = """目录
第一条 工程概况 …… 1
第二条 合同价款 …… 2
第五条 付款方式 …… 12
第十二条 技术要求 …… 20
第十九条 争议的解决 …… 85
第二十条 其他 …… 86"""


def _pages(toc: str) -> list[dict]:
    return [{"page": 1, "text": "虚构项目电梯安装合同文件"}, {"page": 2, "text": toc}]


def test_count_toc_puts_clause_sections_first():
    order = toc_priority_pages(_pages(_COUNT_TOC), 230, {"dispute"})
    # 本合同书（第 3–7 页附近）在前，专用条件 / 通用条件（第 68–93 页附近）随后；信函、技术规范、图纸不在其中
    assert order[:3] == [1, 2, 3]
    assert 69 in order and 85 in order and 91 in order
    assert order.index(85) > order.index(7)
    assert 30 not in order and 150 not in order


def test_start_toc_puts_target_clause_first():
    order = toc_priority_pages(_pages(_START_TOC), 120, {"dispute", "payment"})
    # 标题写着价款 / 付款 / 争议的条目按文档顺序排前（第 2、12、85 页起），技术要求（第 20 页起）不在其中
    assert order[0] == 2
    assert order.index(12) < order.index(85)
    assert 30 not in order and 60 not in order


def test_no_toc_or_unusable_toc_keeps_order():
    assert toc_priority_pages([{"page": 1, "text": "第一条 工程概况"}], 50, {"dispute"}) == []
    # 只有章名、没有页码的目录
    assert toc_priority_pages(_pages("目 录 第十三章 工程款的核实与支付 第二十章 争议、违约及索赔"), 50, {"dispute"}) == []
    # 页码倒退：多半没解析对，不用
    bad = "目录\n第一条 付款方式 …… 30\n第二条 争议解决 …… 5\n第三条 其他 …… 8"
    assert toc_priority_pages(_pages(bad), 50, {"dispute"}) == []


def test_sub_toc_page_numbers_are_ignored():
    # 分目录写「SC/14」「2 of 2」这类分册页码，页码另起，不能当 PDF 页码用
    sub = "专用条件\n目录\n16.9 付款方式 SC/13\n19. 争端的解决 SC/14\n20.1 发出通知和指示 SC/14\nSC/IDX/2 of 2"
    assert toc_priority_pages(_pages(sub), 230, {"dispute"}) == []


# ── 节点：按目录先读专用条件，50 页上限内读到第 91 页的仲裁机构 ──────────────────────────
_PAGE_PAY = "6. 付款方式：电梯验收合格后30日内，雇主支付至合同总价的95%。"
_PAGE_19 = "19. 争端的解决：此类争端均应采用相类于主合同解决争议方式处理。"
_PAGE_APPENDIX = "附录 仲裁机构 19 虚构国际经济贸易仲裁委员会"
_CLAUSES = {
    "payment_clause_text": {"value": "电梯验收合格后30日内，雇主支付至合同总价的95%", "src": "《安装合同.pdf》"},
    "dispute_clause_location": {"value": "专用条件第19条", "src": "《安装合同.pdf》"},
    "dispute_clause_text": {"value": "仲裁机构 19 虚构国际经济贸易仲裁委员会", "src": "《安装合同.pdf》"},
}


def _long_contract_ocr():
    pages = {2: _COUNT_TOC, 5: _PAGE_PAY, 85: _PAGE_19, 91: _PAGE_APPENDIX}

    async def fake(_pdf, n):
        return pages.get(n, f"技术参数表第{n}页")

    return AsyncMock(side_effect=fake)


def _state(page_count: int) -> dict:
    fid = file_store.put(b"%PDF-fake-bytes")
    return {
        "extracted_fields": {
            "contract_sign_date": {"value": "2021年5月28日", "src": "《安装合同.pdf》盖章页"},
            "contract_party_b": {"value": "某电梯公司分公司", "src": "《安装合同.pdf》盖章页"},
            "payment_clause_text": {"value": None, "src": ""},
            "breach_interest_rate_text": {"value": None, "src": ""},
            "dispute_clause_text": {"value": None, "src": ""},
        },
        "files": [{
            "filename": "安装合同.pdf", "identified_type": "合同", "is_scanned": True, "text": "",
            "pages": [], "page_count": page_count, "file_id": fid,
        }],
    }


def test_long_contract_reads_clause_sections_by_toc(monkeypatch):
    ocr = _long_contract_ocr()
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    out = asyncio.run(nodes_mod.ocr_augment_node(_state(230)))
    read = [c.args[1] for c in ocr.await_args_list]
    assert read[:5] == [1, 2, 3, 4, 5]
    assert 91 in read and len(read) <= 50  # 仲裁机构所在的附录页在页数上限内读到
    assert not any(100 < n for n in read)  # 技术规范、图纸没读
    f = out["extracted_fields"]
    assert "仲裁委员会" in f["dispute_clause_text"]["value"]
    assert "页数已达上限" not in str(f["dispute_clause_text"].get("src"))


def test_without_toc_reading_order_unchanged(monkeypatch):
    # 第一批没有目录页：照原顺序逐批读（与改动前一致）
    pages = {3: _PAGE_PAY, 12: "第七条 争议：向工程所在地人民法院提起诉讼。"}

    async def fake(_pdf, n):
        return pages.get(n, f"第{n}页正文")

    ocr = AsyncMock(side_effect=fake)
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    asyncio.run(nodes_mod.ocr_augment_node(_state(60)))
    assert [c.args[1] for c in ocr.await_args_list] == list(range(1, 16))
