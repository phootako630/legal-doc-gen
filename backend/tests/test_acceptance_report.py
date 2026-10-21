# 扫描验收报告 OCR 后取值（批准日期 / 安装地点 / 台数）与 report_ocr 节点单测（虚构数据）
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store
from app.services.acceptance_report import first_install_address, latest_approved_date


def _report_page(no: str, approved: str, place: str = "某某市某某路88号1栋") -> str:
    # 模拟 OCR 把检验报告首页表格识别成 markdown 表格的样子
    return (
        f"报告编号：TJ-E2022{no}\n电梯监督检验报告\n"
        "| 设备品种 | 曳引驱动乘客电梯 | 型号 | HGE-1050 |\n"
        f"| 产品编号 | 20G1{no} | 制造日期 | 2022年06月28日 |\n"
        f"| 安装地点 | {place} | 使用登记证编号 | / |\n"
        "| 检验日期 | 2022年12月01日 | 下次检验日期 | 2023年12月 |\n"
        "| 编 制：某某 | 日期：2022年12月02日 |\n"
        f"| 批 准：某某 | 日期：{approved} |\n"
    )


def test_latest_approved_date_takes_latest_batch():
    pages = [
        {"page": 1, "text": _report_page("01", "2022年12月20日")},
        {"page": 2, "text": _report_page("02", "2022年12月26日")},
        {"page": 3, "text": _report_page("03", "2022年11月3日")},
    ]
    # 不取编制 / 检验 / 制造日期，只取「批准」后的日期，多份取最晚
    assert latest_approved_date(pages) == ("2022年12月26日", 2)


def test_first_install_address():
    pages = [{"page": 1, "text": _report_page("01", "2022年12月20日", "某县城关镇某路1号")}]
    assert first_install_address(pages) == ("某县城关镇某路1号", 1)
    assert first_install_address([{"page": 1, "text": "| 安装地点 | / |"}]) is None


def _scanned_report(page_count: int) -> dict:
    return {
        "filename": "验收报告.pdf",
        "identified_type": "验收报告",
        "is_scanned": True,
        "text": "",
        "pages": [],
        "page_count": page_count,
        "file_id": file_store.put(b"%PDF-fake-report"),
    }


def _run(monkeypatch, fields, page_count=3):
    texts = {
        1: _report_page("01", "2022年12月20日", "某某市某某路88号1栋"),
        2: _report_page("02", "2022年12月26日"),
        3: _report_page("03", "2022年12月22日"),
    }

    async def fake(_pdf, n):
        return texts[n]

    ocr = AsyncMock(side_effect=fake)
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    state = {"extracted_fields": fields, "files": [_scanned_report(page_count)]}
    return asyncio.run(nodes_mod.report_ocr_node(state)), ocr


def test_report_ocr_fills_date_address_and_qty(monkeypatch):
    fields = {
        "acceptance_latest_date": {
            "value": "2022年12月26日",
            "src": "《审批表.pdf》政府验收时间（验收报告未识别，待核实）",
        },
        "install_address": {"value": None, "src": ""},
        "elevator_qty_by_acceptance": {"value": None, "src": ""},
    }
    out, ocr = _run(monkeypatch, fields)
    assert ocr.await_count == 3
    f = out["extracted_fields"]
    assert f["acceptance_latest_date"]["value"] == "2022年12月26日"
    assert "第2页批准日期" in f["acceptance_latest_date"]["src"]
    assert f["install_address"]["value"] == "某某市某某路88号1栋"
    assert f["elevator_qty_by_acceptance"]["value"] == 3  # 按报告首页计数
    assert out["files"][0]["text"]


def test_report_ocr_skipped_when_no_unread_scanned_report(monkeypatch):
    ocr = AsyncMock()
    monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    report = _scanned_report(3)
    # 已 OCR 过：三页都有文字（按页判断读没读完，只有 text 不算，#79 评审）
    report["pages"] = [{"page": n, "text": f"已 OCR 过的第{n}页文本，长度足够算作有文字的页"} for n in (1, 2, 3)]
    report["text"] = "\n\n".join(p["text"] for p in report["pages"])
    out = asyncio.run(nodes_mod.report_ocr_node({"extracted_fields": {}, "files": [report]}))
    assert out == {} and ocr.await_count == 0


def _text_report(approved: str) -> dict:
    # 文本层报告：1 台（设备代码计数），批准日期由初抽从文本取得
    return {
        "filename": "文本报告.pdf",
        "identified_type": "验收报告",
        "is_scanned": False,
        "text": f"设备代码：31104301202100001\n批 准：某某 日期：{approved}",
        "pages": [],
        "page_count": 1,
    }


def test_mixed_text_and_scanned_reports_aggregate_all(monkeypatch):
    # 文本报告 2025-05-01 比扫描报告都晚：日期保留文本报告的；台数 = 文本 1 + 扫描 3
    fields = {
        "acceptance_latest_date": {"value": "2025年5月1日", "src": "《文本报告.pdf》批准日期"},
        "elevator_qty_by_acceptance": {"value": 1, "src": "代码计数"},
    }

    async def fake(_pdf, n):
        return _report_page(f"0{n}", "2024年5月1日")

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    state = {"extracted_fields": fields, "files": [_text_report("2025年5月1日"), _scanned_report(3)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    assert f["acceptance_latest_date"]["value"] == "2025年5月1日"
    assert f["elevator_qty_by_acceptance"]["value"] == 4
    assert "文本报告 1 台 + 《验收报告.pdf》按报告首页计数 3 份" in f["elevator_qty_by_acceptance"]["src"]


def test_scanned_report_later_than_text_report_wins(monkeypatch):
    fields = {"acceptance_latest_date": {"value": "2024年5月1日", "src": "《文本报告.pdf》"}}

    async def fake(_pdf, n):
        return _report_page(f"0{n}", "2025年5月1日")

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    state = {"extracted_fields": fields, "files": [_text_report("2024年5月1日"), _scanned_report(2)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    assert f["acceptance_latest_date"]["value"] == "2025年5月1日"


def test_budget_exhausted_marks_incomplete(monkeypatch):
    monkeypatch.setattr(nodes_mod, "AGENT_MAX_REPORT_OCR_PAGES", 2)

    async def fake(_pdf, n):
        return _report_page(f"0{n}", "2022年12月20日")

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    state = {"extracted_fields": {}, "files": [_scanned_report(5)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    assert f["elevator_qty_by_acceptance"]["value"] == 2
    assert "可能不全" in f["elevator_qty_by_acceptance"]["src"]
    assert "可能不全" in f["acceptance_latest_date"]["src"]


def test_count_first_pages_survives_cropped_report_number_and_html():
    from app.services.acceptance_report import count_report_first_pages

    pages = [
        # 报告编号被裁掉、OCR 输出成 HTML 表格：仍按首页「设备品种」栏计数
        {"page": 1, "text": "<table><tr><td>设备品种</td><td>曳引驱动乘客电梯</td></tr>"
         "<tr><td>安装地点</td><td>某某市某路1号</td></tr></table>"},
        {"page": 2, "text": "| **设备品种** | 曳引驱动乘客电梯 |\n| **产品编号** | 20G1001 |"},
        # 报告后续页没有「设备品种」，不算一份
        {"page": 3, "text": "检验项目及其内容 | 检验结果 | 检验结论"},
    ]
    assert count_report_first_pages(pages) == 2
    assert first_install_address(pages) == ("某某市某路1号", 1)


def test_existing_text_date_flagged_when_scan_incomplete_and_no_date(monkeypatch):
    # 扫描报告 2 页、预算只够 1 页，读到的首页又没有批准日期：文本日期保留但标待核实（#44 复审）
    monkeypatch.setattr(nodes_mod, "AGENT_MAX_REPORT_OCR_PAGES", 1)

    async def fake(_pdf, n):
        return "| 设备品种 | 曳引驱动乘客电梯 |\n| 检验结论 | 合格 |"

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    fields = {"acceptance_latest_date": {"value": "2024年1月1日", "src": "《文本报告.pdf》批准日期"}}
    state = {"extracted_fields": fields, "files": [_text_report("2024年1月1日"), _scanned_report(2)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    node = f["acceptance_latest_date"]
    assert node["value"] == "2024年1月1日"
    assert "待核实" in node["src"] and "可能不全" in node["src"]


def test_scanned_report_without_first_page_label_falls_back_to_report_numbers(monkeypatch):
    # 扫描页「设备品种」被裁掉、报告编号清晰：按编号计数，与文本报告合并为 2 台
    async def fake(_pdf, n):
        return "报告编号：BBB222222\n批 准：某某 日期：2024年1月2日"

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    text = _text_report("2024年1月1日")
    text["text"] = "报告编号：AAA111111\n批 准：某某 日期：2024年1月1日"
    state = {"extracted_fields": {}, "files": [text, _scanned_report(1)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    assert f["elevator_qty_by_acceptance"]["value"] == 2
    assert "按设备代码 / 报告编号计数 1 台" in f["elevator_qty_by_acceptance"]["src"]


def test_pure_scanned_without_label_or_number_marks_incomplete(monkeypatch):
    async def fake(_pdf, n):
        return "模糊不清的照片"

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake))
    fields = {"elevator_qty_by_acceptance": {"value": 3, "src": "初抽"}}
    state = {"extracted_fields": fields, "files": [_scanned_report(1)]}
    f = asyncio.run(nodes_mod.report_ocr_node(state))["extracted_fields"]
    assert "可能不全" in f["elevator_qty_by_acceptance"]["src"]



def test_merged_and_single_scanned_reports_not_double_counted():
    # 律师新案例（中交一公局）：合并版里是同一批报告首页，按设备代码去重，不算成两倍台数
    def first_page(code: str) -> str:
        return f"监督检验报告 设备品种：曳引驱动乘客电梯 设备代码 | {code} 批 准：某某 日期：2025年6月23日"

    codes = ["31103101182025060054", "31103101182025060055", "31103101182025060056"]
    files = []
    for n, code in enumerate(codes):
        f = _scanned_report(1)
        f["filename"] = f"单份报告{n}.pdf"
        f["pages"] = [{"page": 1, "text": first_page(code)}]
        files.append(f)
    merged = _scanned_report(len(codes))
    merged["filename"] = "合并版验收报告.pdf"
    merged["pages"] = [{"page": n + 1, "text": first_page(c)} for n, c in enumerate(codes)]
    files.append(merged)
    fields: dict = {}
    nodes_mod._merge_acceptance_qty(fields, files, files, "")
    node = fields["elevator_qty_by_acceptance"]
    assert node["value"] == 3
    assert "重复" in node["src"]


def test_first_pages_without_ids_still_counted():
    # 照片裁掉设备代码 / 报告编号：读不到标识的首页照常计数，不当成重复
    f = _scanned_report(2)
    f["pages"] = [{"page": n, "text": "监督检验报告 设备品种：曳引驱动乘客电梯"} for n in (1, 2)]
    fields: dict = {}
    nodes_mod._merge_acceptance_qty(fields, [f], [f], "")
    assert fields["elevator_qty_by_acceptance"]["value"] == 2


def _first(text: str) -> str:
    return f"监督检验报告 设备品种：曳引驱动乘客电梯 {text}"


def _qty(*page_lists: list[str]) -> int:
    files = []
    for n, texts in enumerate(page_lists):
        f = _scanned_report(len(texts))
        f["filename"] = f"报告{n}.pdf"
        f["pages"] = [{"page": i + 1, "text": _first(t)} for i, t in enumerate(texts)]
        files.append(f)
    fields: dict = {}
    nodes_mod._merge_acceptance_qty(fields, files, files, "")
    return fields["elevator_qty_by_acceptance"]["value"]


def test_report_numbers_with_brackets_are_not_truncated():
    # 「TJ2024(001)」「TJ2024(002)」是两台，不能截成同一个前缀（#81 评审）
    assert _qty(["报告编号：TJ2024(001)"], ["报告编号：TJ2024(002)"]) == 2


def test_duplicate_first_pages_within_one_file_counted_once():
    # 同一份合并 PDF 里重复的首页也只算一份（#81 评审）
    code = "设备代码 | 31103101182025060054"
    assert _qty([code, code, "设备代码 | 31103101182025060055"]) == 2


def test_copy_with_only_report_number_matches_either_order():
    # 一张拍到设备代码 + 报告编号，另一张只拍到同一报告编号：算一份，上传顺序不影响结果（#81 评审）
    full = "设备代码 | 31103101182025060054 报告编号：JS202506001"
    partial = "报告编号：JS202506001"
    assert _qty([full], [partial]) == 1
    assert _qty([partial], [full]) == 1
