# 一个案件有多份验收报告文件（每台一份 PDF / 文字版与扫描版混合）：台数与最晚验收日期按全部报告汇总。虚构数据。
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.services import file_store


def _report(no: int, code: str, date: str) -> str:
    return (
        f"电梯监督检验报告\n报告编号：JD2024-{no:03d}\n设备品种：曳引驱动乘客电梯\n"
        f"设备代码：{code}\n安装地点：虚构市虚构区建设路{no}号\n检验结论：合格\n"
        f"批准：王某某 日期：{date}\n"
    )


def _text_file(name: str, *reports: str) -> dict:
    pages = [{"page": i + 1, "text": t} for i, t in enumerate(reports)]
    return {"filename": name, "identified_type": "验收报告", "is_scanned": False,
            "text": "\n\n".join(reports), "pages": pages, "file_id": None}


_FILES = [
    _text_file("1号梯监检报告.pdf", _report(1, "31104401002024000001", "2024年3月5日")),
    _text_file("2号梯监检报告.pdf", _report(2, "31104401002024000002", "2024年4月18日")),
    _text_file("3-4号梯监检报告.pdf",
               _report(3, "31104401002024000003", "2024年3月20日"),
               _report(4, "31104401002024000004", "2024年3月21日")),
]


def _extract(monkeypatch, llm_fields, files):
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value=llm_fields))
    return asyncio.run(nodes_mod.extract_node({"files": files, "checklist": {}}))["extracted_fields"]


def test_text_reports_across_files_count_and_latest_date(monkeypatch):
    # LLM 只看了第一份报告的日期：代码按全部报告的「批准」日期取最晚
    f = _extract(monkeypatch, {"acceptance_latest_date": {"value": "2024年3月5日", "src": "《1号梯监检报告.pdf》"}}, _FILES)
    assert f["elevator_qty_by_acceptance"]["value"] == 4
    assert f["acceptance_latest_date"]["value"] == "2024年4月18日"
    assert f["acceptance_latest_date"]["src"].startswith("《2号梯监检报告.pdf》")


def test_duplicate_report_uploaded_twice_counts_once(monkeypatch):
    dup = _FILES + [_text_file("2号梯监检报告(1).pdf", _report(2, "31104401002024000002", "2024年4月18日"))]
    f = _extract(monkeypatch, {}, dup)
    assert f["elevator_qty_by_acceptance"]["value"] == 4


def test_llm_date_kept_when_reports_have_no_approval_date(monkeypatch):
    files = [_text_file("监检报告.pdf", "电梯监督检验报告\n设备代码：31104401002024000009\n检验日期：2024年5月1日")]
    f = _extract(monkeypatch, {"acceptance_latest_date": {"value": "2024年5月1日", "src": "《监检报告.pdf》"}}, files)
    assert f["acceptance_latest_date"]["value"] == "2024年5月1日"


def test_text_and_scanned_reports_combined(monkeypatch):
    # 文字版 2 份（各 1 台）+ 扫描版 1 份（拍照 2 台，批准日期更晚）：台数 4、日期取扫描版的
    scanned_pages = {
        1: "电梯监督检验报告 | 设备品种 | 曳引驱动乘客电梯 | 批准 | 李某 | 日期：2024年6月2日",
        2: "电梯监督检验报告 | 设备品种 | 曳引驱动乘客电梯 | 批准 | 李某 | 日期：2024年6月1日",
    }

    async def fake_ocr(_pdf, n):
        return scanned_pages[n]

    monkeypatch.setattr(nodes_mod, "ocr_page", AsyncMock(side_effect=fake_ocr))
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(return_value={}))
    scanned = {"filename": "5-6号梯报告照片.pdf", "identified_type": "验收报告", "is_scanned": True,
               "text": "", "pages": [], "page_count": 2, "file_id": file_store.put(b"%PDF-fake")}
    files = _FILES[:2] + [scanned]
    fields = asyncio.run(nodes_mod.extract_node({"files": files, "checklist": {}}))["extracted_fields"]
    assert fields["elevator_qty_by_acceptance"]["value"] == 2
    assert fields["acceptance_latest_date"]["value"] == "2024年4月18日"
    out = asyncio.run(nodes_mod.report_ocr_node({"extracted_fields": fields, "files": files}))
    f = out["extracted_fields"]
    assert f["elevator_qty_by_acceptance"]["value"] == 4
    assert f["acceptance_latest_date"]["value"] == "2024年6月2日"
