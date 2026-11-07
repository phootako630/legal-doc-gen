# 线上案件快照端到端（#157 第 3 步）：线上流程（录制开着）→ 导出 eval 案卷 → 严格回放。
# 模型在 HTTP 客户端这一层替换，录制器真实地录下调用；回放不调用任何接口、全部命中，
# 律师改过的字段判「错误」、其余「正确」。全部虚构数据。
import asyncio
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from starlette.datastructures import UploadFile

from app import config
from app.agent.runner import run_analyze, run_resume
from app.routers.generate import GenerateRequest, generate
from app.services import llm_client, ocr_engine, run_log
from app.services.file_parser import parse_file
from scripts.case_eval.runner import run_case
from scripts.case_eval.scoring import CORRECT, WRONG
from scripts.case_eval.snapshot_export import completed_runs, export_case

_FIELDS = {
    "defendant_name": {"value": "虚构丁戊置业有限公司", "src": "《审批表.pdf》"},
    "contract_no": {"value": "XG-2099-0001", "src": "《审批表.pdf》"},
    "total_amount": {"value": 1000.0, "src": "《审批表.pdf》"},
    "paid_amount": {"value": 600.0, "src": "《审批表.pdf》"},
    "unpaid_amount": {"value": 300.0, "src": "《审批表.pdf》"},  # 不勾稽 → 冲突断点
}


def _fake_llm(**kwargs):
    if kwargs.get("response_format"):
        content = json.dumps(_FIELDS, ensure_ascii=False)
    else:
        content = "说明\n---HIGHLIGHT---\n- 1"
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def _pdf(text=None):
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    else:
        page.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(0.2, 0.2, 0.2))  # 扫描件：只有图形，没有文字层
    return doc.tobytes()


@pytest.fixture
def online(tmp_path, monkeypatch):
    """线上环境：快照与录制开着，模型与 OCR 在 HTTP 客户端这一层替换。"""
    table = tmp_path / "branch_info.json"
    table.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(config, "BRANCH_INFO_PATH", str(table))
    monkeypatch.setattr(llm_client, "_primary_down_until", 0.0)
    create = AsyncMock(side_effect=lambda **kw: _fake_llm(**kw))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client, "_get_client", lambda: client)
    body = {"output": {"choices": [{"message": {"content": [{"text": "电梯验收报告 虚构项目"}]}}]}}
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: SimpleNamespace(json=lambda: body))
    monkeypatch.setattr(ocr_engine, "_keys", lambda: ["k"])
    return tmp_path


def _lawyer_session(files):
    async def go():
        run_log.set_user("lawyer-a")
        state = await run_analyze(files, True, "lawyer-a")
        resumed = await run_resume(state.run_id, {"total_amount": 900.0}, "lawyer-a")
        final = {k: dict(v) for k, v in resumed.extracted_fields.items()}
        final["contract_no"] = {"value": "XG-2099-0002", "src": "律师填写"}  # 律师改了一处
        await generate(GenerateRequest(validated_json=final, run_id=state.run_id))
        return state.run_id

    return asyncio.run(go())


def test_online_case_exports_and_replays(online, monkeypatch):
    run_log.set_run_id(None)
    uploads = [
        asyncio.run(parse_file(UploadFile(file=io.BytesIO(_pdf("Shenpibiao 1000 600 300")), filename="审批表.pdf"))),
        asyncio.run(parse_file(UploadFile(file=io.BytesIO(_pdf()), filename="001.pdf"))),  # 首页 OCR 认类型
    ]
    assert uploads[1]["identified_type"] == "验收报告" and uploads[1]["is_scanned"]
    uploads[0]["identified_type"] = "审批表"  # 律师在上传页确认的类型
    uploads[0]["detected_type"] = "未知"
    run_id = _lawyer_session(uploads)

    snapshots, out = str(online / "snapshots"), str(online / "exported")
    assert completed_runs(snapshots) == [run_id]
    info = export_case(snapshots, run_id, out)
    case_dir = os.path.join(out, info["case"])
    assert run_id[:8] in info["case"] and info["edited"] == ["contract_no"] and info["warnings"] == []
    assert sorted(os.listdir(os.path.join(case_dir, "materials"))) == ["001.pdf", "审批表.pdf"]
    expected = json.load(open(os.path.join(case_dir, "expected.json"), encoding="utf-8"))
    assert expected["internet_allowed"] is True and expected["decisions"] == {"total_amount": 900.0}
    assert expected["material_types"] == {"审批表.pdf": "审批表"}
    assert "系统交付值：XG-2099-0001" in expected["fields"]["contract_no"]["note"]
    with pytest.raises(ValueError, match="已存在"):
        export_case(snapshots, run_id, out)  # 不覆盖已导出的案件

    # 回放：录制关掉、模型与 OCR 一旦被调用就失败
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(llm_client, "_get_client", lambda: pytest.fail("严格回放不该调用模型"))
    monkeypatch.setattr(ocr_engine, "_post_with_fallback", lambda payload: pytest.fail("严格回放不该调用 OCR"))
    result = asyncio.run(run_case(case_dir, mode="replay", strict=True))
    assert result["error"] is None and result["stats"]["misses"] == []
    verdicts = {r["key"]: r["verdict"] for r in result["results"]}
    assert verdicts.pop("contract_no") == WRONG  # 律师改过的字段：系统当时给错了
    assert set(verdicts.values()) == {CORRECT}, verdicts


def test_expected_fields_from_lawyer_final():
    from scripts.case_eval.snapshot_export import _expected_fields

    delivered = {
        "a": {"value": "系统值"}, "b": {"value": "相同"}, "c": {"value": "系统给了"},
        "d": {"value": None}, "contacts": [{"name": {"value": "张三"}, "phone": {"value": "1"}}],
    }
    final = {
        "a": {"value": "律师值"}, "b": {"value": " 相同 "}, "c": {"value": None},  # 律师清空了
        "d": {"value": None},  # 两边都空：没有标准答案，不写
        "contacts": [{"name": {"value": "张三"}, "phone": {"value": "1"}}],
    }
    fields, edited = _expected_fields(final, delivered)
    assert edited == ["a", "c"]
    assert fields["a"] == {"value": "律师值", "note": "律师在审核页改过（系统交付值：系统值）"}
    assert fields["b"] == {"value": " 相同 "} and fields["c"]["value"] is None and "d" not in fields
    assert fields["contacts"] == {"value": "张三（1）"}


def test_export_warns_on_missing_material_and_changed_prompts(online, monkeypatch):
    run_log.set_run_id(None)
    uploads = [asyncio.run(parse_file(UploadFile(file=io.BytesIO(_pdf("Shenpibiao")), filename="审批表.pdf")))]
    run_id = _lawyer_session(uploads)
    snapshots = str(online / "snapshots")
    for name in os.listdir(os.path.join(snapshots, "materials")):
        os.remove(os.path.join(snapshots, "materials", name))  # 原件过了保留期
    from app.services import case_snapshot

    monkeypatch.setattr(case_snapshot, "prompts_digest", lambda: "changed")
    info = export_case(snapshots, run_id, str(online / "out"))
    assert any("缺原件" in w for w in info["warnings"])
    assert any("提示词模板已改过" in w for w in info["warnings"])


def test_unfinished_cases_not_exported(online):
    from app.services import case_snapshot

    case_snapshot.start("a" * 32, [], True, "lawyer-a")  # 律师还没导出起诉状
    assert completed_runs(str(online / "snapshots")) == []
    with pytest.raises(ValueError, match="还没导出起诉状"):
        export_case(str(online / "snapshots"), "a" * 32, str(online / "out"))


def test_cli_exports_and_skips_existing(online, monkeypatch, capsys):
    import sys

    from scripts import export_snapshots

    run_log.set_run_id(None)
    uploads = [asyncio.run(parse_file(UploadFile(file=io.BytesIO(_pdf("Shenpibiao")), filename="审批表.pdf")))]
    run_id = _lawyer_session(uploads)
    argv = ["export_snapshots.py", str(online / "snapshots"), str(online / "cli-out")]
    monkeypatch.setattr(sys, "argv", argv)
    export_snapshots.main()
    out = capsys.readouterr().out
    assert f"-{run_id[:8]}" in out and "律师改过 1 个：contract_no" in out and "共导出 1 个案件" in out
    export_snapshots.main()  # 再跑一次：已导出过的跳过，不覆盖
    assert "跳过 1 个" in capsys.readouterr().out
