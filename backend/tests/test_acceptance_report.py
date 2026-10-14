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


def test_report_ocr_skipped_when_report_already_read(monkeypatch):
    fields = {
        "acceptance_latest_date": {"value": "2022年12月26日", "src": "《验收报告.pdf》第1页"},
        "elevator_qty_by_acceptance": {"value": 3, "src": "代码计数"},
    }
    out, ocr = _run(monkeypatch, fields)
    assert out == {} and ocr.await_count == 0


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
