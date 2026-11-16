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


def test_footer_total_pages_does_not_switch_to_count_mode():
    # 页码目录的页脚写「文件总页数：120」，不能因此当成页数目录；目录只列少数条目时，
    # 付款条目只先读前几页，争议条目（第 85 页）仍排在页数上限内（#174 评审）
    toc = "目录\n付款方式……12\n争议解决……85\n其他……86\n文件总页数：120"
    order = toc_priority_pages(_pages(toc), 120, {"dispute", "payment"})
    assert order[0] == 12
    assert 85 in order and order.index(85) < 15


def test_count_toc_without_divider_pages():
    # 页数目录、各部分没有分隔页：8 个技术部分各 10 页后，争议部分从第 83 页开始，估算范围要覆盖到（#174 评审）
    rows = "\n".join(f"| {i} | 技术部分{i} | 10 |" for i in range(1, 9))
    toc = f"合同目录\n| 序号 | 文件内容 | 页数 |\n{rows}\n| 9 | 专用条件（争议解决） | 6 |"
    order = toc_priority_pages(_pages(toc), 120, {"dispute"})
    assert 83 in order and 88 in order  # 无分隔页时第 83–88 页；有分隔页时更靠后，也都在范围内
    assert order[0] <= 83


def test_count_header_only_from_header_row():
    # 不带竖线的表头「序号 文件内容 页数」照样认作页数写法
    toc = "合同目录\n序号 文件内容 页数\n1 合同协议书 3\n2 技术参数表 50\n3 专用条件 6"
    order = toc_priority_pages(_pages(toc), 70, {"dispute"})
    assert 56 in order and 62 in order and 30 not in order


def test_plain_header_with_title_column_is_count_mode():
    # 不带竖线的「序号 标题 页数」表头同样是页数写法；页数 50 → 6 不是页码倒退（#174 复审）
    toc = "合同目录\n序号 标题 页数\n1 合同协议书 3\n2 技术参数表 50\n3 专用条件 6"
    order = toc_priority_pages(_pages(toc), 70, {"dispute"})
    assert 56 in order and 62 in order and 30 not in order
    # 页脚「文件总页数：120」仍不算表头
    footer = "目录\n付款方式……12\n争议解决……85\n其他……86\n文件总页数：120"
    assert toc_priority_pages(_pages(footer), 120, {"dispute"})[0] == 85


def test_start_toc_reads_rest_of_target_entry_before_other_parts():
    # 「争议解决……85」下一条「其他……100」：第 85 页只是转引、第 94 页附录才写仲裁机构。
    # 先读各目标条目前几页，再读条目其余页，第 94 页仍在优先列表里、且排在付款条目后段之前（#174 复审）
    toc = "目录\n付款方式……12\n技术要求……20\n争议解决……85\n其他……100"
    order = toc_priority_pages(_pages(toc), 150, {"dispute", "payment"})
    assert order.index(85) < 15
    assert 94 in order
    assert order.index(12) < order.index(85) < order.index(94)
    assert 50 not in order and 120 not in order


# ── 按目录读完条款部分仍没有违约条款：按合同未约定写逾期付款利息 + LPR，标待核实（用户选定，2026-11-16）──
def _budget_hit_state(page_count: int) -> dict:
    state = _state(page_count)
    # 签约日期还是审批表的：要找签章页，找不到就一直读到页数上限
    state["extracted_fields"]["contract_sign_date"] = {"value": "2021年5月28日", "src": "《审批表.pdf》签约时间栏"}
    return state


def test_toc_sections_read_breach_missing_is_treated_as_not_agreed(monkeypatch):
    from app.services.derived_fields import (
        LPR_INTEREST_BASIS,
        OCR_BUDGET_HINT,
        interest_basis_field,
        interest_term_field,
    )

    monkeypatch.setattr(nodes_mod, "ocr_page", _long_contract_ocr())
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    out = asyncio.run(nodes_mod.ocr_augment_node(_budget_hit_state(230)))
    f = out["extracted_fields"]
    src = f["breach_interest_rate_text"]["src"]
    assert "已读完条款部分" in src and OCR_BUDGET_HINT not in src and "待核实" in src
    term, basis = interest_term_field(f), interest_basis_field(f)
    assert term["value"] == "逾期付款利息" and "待核实" in term["src"]
    assert basis["value"] == LPR_INTEREST_BASIS and "待核实" in basis["src"]


def test_budget_hit_without_toc_still_leaves_interest_to_lawyer(monkeypatch):
    # 没有目录可依：页数用完时仍不能认定合同未约定，利息留给律师（#71 原规则不变）
    from app.services.derived_fields import OCR_BUDGET_HINT, interest_term_field

    async def fake(_pdf, n):
        return _PAGE_PAY if n == 3 else ("19. 争议：向工程所在地人民法院提起诉讼。" if n == 4 else f"第{n}页正文")

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    out = asyncio.run(nodes_mod.ocr_augment_node(_budget_hit_state(230)))
    f = out["extracted_fields"]
    assert OCR_BUDGET_HINT in f["breach_interest_rate_text"]["src"]
    assert interest_term_field(f)["value"] is None


def test_other_main_contract_unread_keeps_budget_hint(monkeypatch):
    # 两份扫描主合同：A 按目录读完条款部分，B 没目录、没读完；不能替 B 断定「未约定」，仍写页数上限提示（#175 评审）
    from app.services.derived_fields import OCR_BUDGET_HINT, interest_term_field

    fid = file_store.put(b"%PDF-fake-bytes")
    other = {"filename": "补充安装合同.pdf", "identified_type": "合同", "is_scanned": True, "text": "",
             "pages": [], "page_count": 120, "file_id": fid}
    state = _budget_hit_state(230)
    state["files"].append(other)
    monkeypatch.setattr(nodes_mod, "ocr_page", _long_contract_ocr())
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    out = asyncio.run(nodes_mod.ocr_augment_node(state))
    f = out["extracted_fields"]
    assert OCR_BUDGET_HINT in f["breach_interest_rate_text"]["src"]
    assert interest_term_field(f)["value"] is None


def test_breach_clause_with_rate_is_not_treated_as_not_agreed(monkeypatch):
    # 违约条款原文已抽到、写着利率，只是利率字段空着：不写「未见违约条款」，也不套 LPR（#175 评审）
    from app.services.derived_fields import LPR_INTEREST_BASIS, interest_basis_field

    clauses = dict(_CLAUSES)
    clauses["breach_clause_text"] = {"value": "甲方逾期付款的，应按日利率万分之二支付利息", "src": "《安装合同.pdf》"}
    monkeypatch.setattr(nodes_mod, "ocr_page", _long_contract_ocr())
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=clauses))
    out = asyncio.run(nodes_mod.ocr_augment_node(_budget_hit_state(230)))
    f = out["extracted_fields"]
    assert "已读完条款部分" not in str(f["breach_interest_rate_text"].get("src"))
    basis = interest_basis_field(f)
    assert basis["value"] != LPR_INTEREST_BASIS and basis["value"] is None


def test_unfinished_strategic_contract_keeps_budget_hint(monkeypatch):
    # 主合同按目录读完条款部分，但扫描的战略合同 20 页只读了前 10 页：战略合同也可能补出违约条款，不下「未约定」（#175 复审）
    from app.services.derived_fields import OCR_BUDGET_HINT

    fid = file_store.put(b"%PDF-strategic-bytes")
    strategic = {"filename": "战略合作协议.pdf", "identified_type": "战略合同", "is_scanned": True, "text": "",
                 "pages": [], "page_count": 20, "file_id": fid}
    state = _budget_hit_state(230)
    state["files"].append(strategic)
    main_ocr = _long_contract_ocr()

    async def fake(pdf, n):
        # 战略合同没有目录，每页都是正文
        return f"战略合作协议第{n}页" if pdf == b"%PDF-strategic-bytes" else await main_ocr(pdf, n)

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=_CLAUSES))
    out = asyncio.run(nodes_mod.ocr_augment_node(state))
    assert OCR_BUDGET_HINT in out["extracted_fields"]["breach_interest_rate_text"]["src"]


def test_breach_clause_with_fixed_amount_does_not_get_lpr():
    # 违约条款原文写「每日支付100元利息」、利率字段空着且带页数上限提示：不套 LPR，交律师（#175 复审）
    from app.services.derived_fields import LPR_INTEREST_BASIS, OCR_BUDGET_HINT, interest_basis_field

    for clause in ("甲方逾期付款的，每日支付100元利息", "甲方逾期付款的，每日支付壹佰元利息"):
        f = {
            "breach_clause_text": {"value": clause, "src": "《合同.pdf》"},
            "breach_interest_rate_text": {"value": None, "src": f"{OCR_BUDGET_HINT}，合同可能未读完"},
        }
        basis = interest_basis_field(f)
        assert basis["value"] != LPR_INTEREST_BASIS and basis["value"] is None, clause
