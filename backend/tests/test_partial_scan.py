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


# ── 第二批：战略协议分类、战略合同单独预算、审批表买卖 / 安装分栏 ─────────────────
def test_strategic_supplement_filename_is_strategic_contract():
    # 「战略协议补充协议」是战略合同的补充，不是本案合同的补充协议（律师新案例：徐州九里苏宁买卖）
    assert file_parser._identify_type("某集团战略协议补充协议七-2019至2022年.pdf") == "战略合同"
    assert file_parser._identify_type("某项目设备补充协议.pdf") == "补充协议"
    assert file_parser._identify_type("x.pdf", "某集团战略合作协议书之\n补充协议（七）") == "战略合同"


def test_strategic_contract_has_own_ocr_budget(monkeypatch):
    # 主合同把 50 页用完后，战略合同仍按自己的页数上限读到仲裁条款
    fid = file_store.put(b"%PDF-fake")
    main = {"filename": "供货合同.pdf", "identified_type": "合同", "is_scanned": True, "text": "",
            "pages": [], "page_count": 60, "file_id": fid}
    strategic = {"filename": "战略协议补充协议.pdf", "identified_type": "战略合同", "is_scanned": True,
                 "text": "", "pages": [], "page_count": 2, "file_id": fid}
    clause = "第三条 争议：若协商不能达成一致意见的，应把争议事项提交某某仲裁委员会仲裁处理。"

    async def fake(_pdf, n):
        return clause if fake.calls >= 50 else "主合同其他条款页面"  # noqa: B023

    fake.calls = 0

    async def counting(pdf, n):
        res = await fake(pdf, n)
        fake.calls += 1
        return res

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=counting))
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value={
        "dispute_clause_location": {"value": "第三条", "src": "《战略协议补充协议.pdf》"},
        "dispute_clause_text": {"value": "应把争议事项提交某某仲裁委员会仲裁处理", "src": "《战略协议补充协议.pdf》"},
    }))
    state = {
        "extracted_fields": {
            "contract_sign_date": {"value": "2021年1月1日", "src": "《供货合同.pdf》盖章页"},
            "contract_party_b": {"value": "某电梯公司", "src": "《供货合同.pdf》盖章页"},
            "payment_clause_text": {"value": "验收后支付95%", "src": "《供货合同.pdf》"},
            "dispute_clause_text": {"value": None, "src": ""},
        },
        "files": [main, strategic],
    }
    out = asyncio.run(nodes_mod.ocr_augment_node(state))
    assert fake.calls == 52  # 主合同 50 页 + 战略合同 2 页
    assert "仲裁" in out["extracted_fields"]["dispute_clause_text"]["value"]


_DUAL_APPROVAL = (
    "合同类型* 买卖附带安装合同 签约时间* 2021-01-01\n"
    "买卖合同总额* 4,000,000.00 已付款金额* 3,600,000.00 未付款金额* 400,000.00\n"
    "买卖合同未付款项 构成* 货款：验收后应付款 200000 元、 质保金 200000元（质保金付款期限）\n"
    "安装合同总额* 1,000,000.00 已付款金额 900,000.00 未付款金额 100,000.00\n"
    "安装合同未付款项 构成* 工程款：验收款 50000元、 质保金 50000元（质保金付款期限）、 附 加工程款 元\n"
    "其他情况 货到工地时间"
)


def _dual_files(contract_name: str) -> list[dict]:
    return [
        {"filename": "某某诉讼审批流程.pdf", "identified_type": "审批表", "text": _DUAL_APPROVAL},
        {"filename": contract_name, "identified_type": "合同", "text": ""},
    ]


def test_dual_approval_picks_install_section():
    from app.services.extraction import apply_dual_contract_amounts

    fields = {"total_amount": {"value": 5000000, "src": "审批表"}}
    apply_dual_contract_amounts(fields, _dual_files("某项目电梯安装合同-盖章版.pdf"))
    assert fields["total_amount"]["value"] == 1000000
    assert fields["paid_amount"]["value"] == 900000
    assert fields["unpaid_amount"]["value"] == 100000
    assert fields["retention_unpaid_amount"]["value"] == 50000
    assert fields["contract_type"]["value"] == "安装合同"


def test_dual_approval_picks_sale_section():
    from app.services.extraction import apply_dual_contract_amounts

    fields: dict = {}
    apply_dual_contract_amounts(fields, _dual_files("某项目电梯设计及供货合同.pdf"))
    assert fields["total_amount"]["value"] == 4000000
    assert fields["unpaid_amount"]["value"] == 400000
    assert fields["retention_unpaid_amount"]["value"] == 200000
    assert fields["contract_type"]["value"] == "买卖合同"


def test_dual_approval_untouched_when_contract_kind_unclear():
    from app.services.extraction import apply_dual_contract_amounts

    fields = {"total_amount": {"value": 5000000, "src": "审批表"}}
    apply_dual_contract_amounts(fields, _dual_files("合同扫描件0001.pdf"))
    assert fields == {"total_amount": {"value": 5000000, "src": "审批表"}}


def test_dual_approval_skips_when_title_mixes_sale_and_install():
    # 文件名 001.pdf、标题「电梯设备安装合同」：同时有「设备」和「安装」，判断不了，不动（#80 评审）
    from app.services.extraction import apply_dual_contract_amounts

    files = _dual_files("001.pdf")
    files[1]["text"] = "电梯设备安装合同\n甲方：虚构置业有限公司"
    fields = {"unpaid_amount": {"value": 100000, "src": "审批表"}}
    apply_dual_contract_amounts(fields, files)
    assert fields == {"unpaid_amount": {"value": 100000, "src": "审批表"}}


def test_dual_approval_clears_other_section_retention():
    # 安装栏没写质保金：不能留着 LLM 先取的买卖栏质保金（#80 评审）
    from app.services.extraction import apply_dual_contract_amounts

    files = _dual_files("某项目电梯安装合同.pdf")
    files[0]["text"] = _DUAL_APPROVAL.replace("质保金 50000元（质保金付款期限）、", "")
    fields = {"retention_unpaid_amount": {"value": 200000, "src": "审批表"}}
    apply_dual_contract_amounts(fields, files)
    assert fields["retention_unpaid_amount"]["value"] is None
    assert "核实" in fields["retention_unpaid_amount"]["src"]


def test_dual_approval_keeps_ocr_warning_for_scanned_form():
    # 扫描审批表 OCR 出的金额：重建字段后仍带待核实标记（#80 评审）
    from app.services.extraction import apply_dual_contract_amounts

    files = _dual_files("某项目电梯安装合同.pdf")
    files[0]["is_scanned"] = True
    fields: dict = {}
    apply_dual_contract_amounts(fields, files)
    for key in ("total_amount", "paid_amount", "unpaid_amount", "contract_type"):
        assert "OCR" in fields[key]["src"] and fields[key]["channel"] == "ocr"
