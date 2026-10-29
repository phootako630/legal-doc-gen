# 台数核对（律师补充确认单第 2 题）：VGE 家用电梯计数 + 合同与验收报告台数不一致时的断点
import asyncio
from unittest.mock import AsyncMock

from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze, run_resume
from app.services import file_store
from app.services.equipment_list import count_vge_units

_MD_TABLE = """第四部分 合同计价清单
| 序号 | 设备名称 | 型号 | 载重 (kg) | 数量 (台) | 单价 |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 住宅电梯 | LGE-S1000 | 1000 | 10 | 28720 |
| 2 | 家用电梯 | VGE-320 | 320 | 1 | 50000 |
| 3 | 商业电梯 | HGE-S1600 | 1600 | 4 | 31520 |
"""
_HTML_TABLE = (
    "<table><tr><td>序号</td><td>型号</td><td>台数</td></tr>"
    "<tr><td>1</td><td>VGE-A</td><td>2</td></tr>"
    "<tr><td>2</td><td>LGE-B</td><td>3</td></tr></table>"
)


def test_count_vge_markdown():
    assert count_vge_units(_MD_TABLE) == (1, True)


def test_count_vge_html():
    assert count_vge_units(_HTML_TABLE) == (2, True)


def test_count_vge_absent():
    assert count_vge_units("型号 LGE-S1000 数量 10") == (0, False)


def test_count_vge_seen_but_unparsed():
    # 横排表认不出列：只报告出现过 VGE，台数交律师
    assert count_vge_units("| 编号 | 2#DT1 |\n| 型号规格 | VGE-320 |") == (0, True)


# 文字版 PDF 的设备清单（#112）：空格 / 制表符对齐的纯文字，虚构
_PLAIN_TABLE = """二、设备清单
序号  型号      层站      台数
1    HGP       18/18/18  3
2    VGE-2     3/3/3     1
合计                     4
"""


def test_count_vge_plain_text_space_aligned():
    # 层站「3/3/3」「18/18/18」不能当台数；合计行不重复计
    assert count_vge_units(_PLAIN_TABLE) == (1, True)


def test_count_vge_plain_text_tab_separated():
    text = "序号\t型号\t载重\t数量\n1\tVGE 320\t320\t2台\n2\tLGE-1000\t1000\t5台\n"
    assert count_vge_units(text) == (2, True)


def test_count_vge_plain_text_misaligned_row_not_guessed():
    # VGE 行切出的列数与表头不同（层站与台数粘在一起）：不猜，交给律师
    text = "序号  型号      层站      台数\n1    VGE-2     3/3/3 1\n"
    assert count_vge_units(text) == (0, True)


def test_count_vge_plain_text_prose_after_table_ignored():
    # 表格之后的正文提到 VGE 不算一行清单
    text = _PLAIN_TABLE + "说明：VGE 型号为家用电梯，无需取得监督检验报告。\n"
    assert count_vge_units(text) == (1, True)


def _run(monkeypatch, extracted, files, ocr=None):
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(side_effect=[extracted, "无\n---HIGHLIGHT---\n"]),
    )
    if ocr is not None:
        monkeypatch.setattr(nodes_mod, "ocr_page", ocr)
    return asyncio.run(run_analyze(files, True))


def _qty(contract, acceptance):
    return {
        "elevator_qty_by_contract": {"value": contract, "src": "《合同》"},
        "elevator_qty_by_acceptance": {"value": acceptance, "src": "《验收报告》"},
        # 条款与签章页信息齐全，不触发条款 / 签章页 OCR
        "contract_sign_date": {"value": "2024年2月26日", "src": "《合同》盖章页"},
        "contract_party_b": {"value": "某电梯公司", "src": "《合同》盖章页"},
        "payment_clause_text": {"value": "x", "src": "《合同》"},
        "breach_interest_rate_text": {"value": "x", "src": "《合同》"},
        "dispute_clause_text": {"value": "向工程所在地法院起诉", "src": "《合同》"},
    }


def test_vge_explains_difference_no_pause(monkeypatch):
    # 合同 15 台含 VGE 1 台，验收报告 14 台 → 不暂停，按 14 台并提醒家用电梯
    files = [{"filename": "合同.docx", "identified_type": "合同", "text": _MD_TABLE}]
    extracted = _qty(15, 14)
    extracted["elevator_qty_by_contract"]["value"] = 15
    state = _run(monkeypatch, extracted, files)
    assert state.pending is None
    f = state.extracted_fields
    assert f["elevator_qty_vge"]["value"] == 1
    assert f["elevator_qty"]["value"] == 14
    assert "家用电梯" in f["elevator_qty"]["src"]
    assert f["elevator_qty_contract"]["value"] == 15


def test_text_contract_plain_list_vge_no_pause(monkeypatch):
    # 文字版合同的设备清单是空格对齐的纯文字（#112）：合同 4 台含 VGE 1 台、验收 3 台 → 不再停在台数断点
    files = [{"filename": "合同.pdf", "identified_type": "合同", "text": _PLAIN_TABLE}]
    state = _run(monkeypatch, _qty(4, 3), files)
    assert state.pending is None
    f = state.extracted_fields
    assert f["elevator_qty_vge"]["value"] == 1
    assert f["elevator_qty"]["value"] == 3


def test_mismatch_pauses_then_lawyer_decides(monkeypatch):
    files = [{"filename": "合同.docx", "identified_type": "合同", "text": "无设备清单"}]
    state = _run(monkeypatch, _qty(15, 13), files)
    assert state.pending is not None
    assert state.pending.field_keys == ["elevator_qty"]
    assert state.pending.options == ["13", "15"]
    assert "补充协议" in state.pending.question and "漏传" in state.pending.question

    resumed = asyncio.run(run_resume(state.run_id, {"elevator_qty": "13"}))
    assert resumed.pending is None
    assert resumed.extracted_fields["elevator_qty"]["value"] == "13"
    assert resumed.extracted_fields["elevator_qty"]["src"] == "律师确认"


def test_mismatch_ocrs_rest_of_scanned_contract(monkeypatch):
    # 扫描合同只 OCR 过前 2 页 → 补读剩余页找到设备清单（第 3 页）
    fid = file_store.put(b"%PDF-fake")
    files = [
        {
            "filename": "合同.pdf",
            "identified_type": "合同",
            "is_scanned": True,
            "file_id": fid,
            "page_count": 3,
            "text": "",
            "pages": [],
        }
    ]

    async def fake_ocr(_pdf, n):
        return _MD_TABLE if n == 3 else "正文"

    ocr = AsyncMock(side_effect=fake_ocr)
    state = _run(monkeypatch, _qty(15, 14), files, ocr=ocr)
    assert state.pending is None
    assert ocr.await_count == 3
    assert state.extracted_fields["elevator_qty_vge"]["value"] == 1


def test_resume_does_not_repeat_ocr(monkeypatch):
    # 断点恢复时 LangGraph 从头重跑被打断的节点：OCR 在断点之前的节点里做完，
    # 律师作答后不会再识别一遍（#70 验收标准）
    fid = file_store.put(b"%PDF-fake")
    files = [
        {
            "filename": "合同.pdf",
            "identified_type": "合同",
            "is_scanned": True,
            "file_id": fid,
            "page_count": 3,
            "text": "",
            "pages": [],
        }
    ]
    ocr = AsyncMock(return_value="正文，无设备清单")
    state = _run(monkeypatch, _qty(15, 13), files, ocr=ocr)
    assert state.pending is not None and state.pending.field_keys == ["elevator_qty"]
    before = ocr.await_count
    assert before > 0

    resumed = asyncio.run(run_resume(state.run_id, {"elevator_qty": "13"}))
    assert resumed.pending is None
    assert ocr.await_count == before
