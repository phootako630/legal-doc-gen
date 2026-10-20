# 文件类型：战略 / 框架合同单独成类、上传页律师改类型（校验取值、记入运行日志）。虚构数据。
import json

from fastapi.testclient import TestClient

from app.agent.nodes import _clause_ocr_targets
from app.main import app
from app.routers.analyze import FileInput, _log_type_overrides
from app.services import run_log
from app.services.file_parser import _identify_type


def test_strategic_contract_is_its_own_type():
    assert _identify_type("某项目电梯战略合同.pdf") == "战略合同"
    assert _identify_type("2024年度框架协议.pdf") == "战略合同"
    assert _identify_type("001.pdf", "电梯采购战略合作协议\n甲方：某公司") == "战略合同"
    # 主合同正文提到「按战略合同执行」仍是合同
    body = "电梯买卖合同\n甲方：某公司\n第十条 争议的解决按战略合同内相关条款执行。"
    assert _identify_type("002.pdf", body) == "合同"
    assert _identify_type("某项目电梯安装合同.pdf") == "合同"


def test_strategic_contract_read_after_main_contract():
    files = [
        {"filename": "战略.pdf", "identified_type": "战略合同", "is_scanned": True, "file_id": "a"},
        {"filename": "主合同.pdf", "identified_type": "合同", "is_scanned": True, "file_id": "b"},
        {"filename": "审批表.pdf", "identified_type": "审批表", "is_scanned": True, "file_id": "c"},
        {"filename": "文字合同.pdf", "identified_type": "合同", "is_scanned": False, "file_id": None},
    ]
    assert [f["filename"] for f in _clause_ocr_targets(files)] == ["主合同.pdf", "战略.pdf"]


def test_analyze_rejects_unknown_type_value():
    res = TestClient(app).post(
        "/api/analyze",
        json={"files": [{"filename": "a.pdf", "text": "x", "identified_type": "随便写"}]},
    )
    assert res.status_code == 422


def test_type_override_logged_without_filename(tmp_path, monkeypatch):
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(log_path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    _log_type_overrides(
        [
            FileInput(filename="某被告公司战略合同.pdf", text="", identified_type="战略合同", detected_type="合同"),
            FileInput(filename="审批表.pdf", text="", identified_type="审批表", detected_type="审批表"),
            FileInput(filename="旧客户端.pdf", text="", identified_type="合同"),
        ]
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "file_type_override"
    assert record["changes"] == [{"from": "合同", "to": "战略合同"}]
    assert "某被告" not in lines[0]
