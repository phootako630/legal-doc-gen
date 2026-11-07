# 线上案件快照（#157）：上传存原件 → 分析建快照 → 断点作答 → 导出起诉状补全；只认自己的会话；
# source_id 不进分析流程；目录权限；保留期清理；未开启时什么都不写。全部虚构数据。
import asyncio
import io
import json
import os
import time
from unittest.mock import AsyncMock

import fitz
import pytest
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app import config
from app.agent import nodes as nodes_mod
from app.agent.runner import case_files, run_analyze, run_resume
from app.routers.analyze import FileInput
from app.routers.generate import GenerateRequest, generate
from app.services import case_snapshot, run_log
from app.services.file_parser import parse_file

_FIELDS = {
    "defendant_name": {"value": "虚构丁戊置业有限公司", "src": "《审批表.pdf》"},
    "total_amount": {"value": 1000.0, "src": "《审批表.pdf》"},
    "paid_amount": {"value": 600.0, "src": "《审批表.pdf》"},
    "unpaid_amount": {"value": 300.0, "src": "《审批表.pdf》"},  # 不勾稽 → 冲突断点
}


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_VERSION", "abc1234")
    root = tmp_path / "snapshots"
    table = tmp_path / "branch_info.json"
    table.write_text('{"虚构": 1}', encoding="utf-8")
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(root))
    monkeypatch.setattr(config, "BRANCH_INFO_PATH", str(table))
    return root


def _pdf_bytes(text):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text, fontname="china-s")
    return doc.tobytes()


def _upload(content, name="审批表.pdf"):
    return asyncio.run(parse_file(UploadFile(file=io.BytesIO(content), filename=name)))


def _flow(monkeypatch, files, user="lawyer-a"):
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[_FIELDS, "说明\n---HIGHLIGHT---\n- 1"]))

    async def go():
        run_log.set_user(user)
        state = await run_analyze(files, True, user)
        assert state.pending is not None
        resumed = await run_resume(state.run_id, {"total_amount": 900.0}, user)
        final = {k: dict(v) for k, v in resumed.extracted_fields.items()}
        final["defendant_name"] = {"value": "虚构丁戊置业发展有限公司", "src": "律师填写"}  # 律师改了
        await generate(GenerateRequest(validated_json=final, run_id=state.run_id))
        return state.run_id, final

    return asyncio.run(go())


def test_full_flow_snapshot(snap, monkeypatch):
    content = _pdf_bytes("Shenpibiao 1000 600 300")
    parsed = _upload(content)
    assert parsed["source_id"] and (snap / "materials" / parsed["source_id"]).read_bytes() == content
    assert _upload(content)["source_id"] == parsed["source_id"]  # 同一份原件只存一次
    assert len(os.listdir(snap / "materials")) == 1

    run_id, final = _flow(monkeypatch, [{**parsed, "identified_type": "审批表", "detected_type": "未知"}])
    case = case_snapshot.load(run_id)
    assert case["files"][0]["source_id"] == parsed["source_id"]
    assert case["files"][0]["identified_type"] == "审批表" and case["files"][0]["detected_type"] == "未知"
    assert case["internet_allowed"] is True and case["prompts"] and case["owner"] == "lawyer-a"
    assert case["code_version"] == "abc1234"  # 部署的提交号（#159 评审）
    assert [d["answers"] for d in case["decisions"]] == [{"total_amount": 900.0}]
    assert case["final"] == final
    assert case["delivered"]["defendant_name"]["value"] == "虚构丁戊置业有限公司"  # 交付值保留，便于看律师改了什么
    assert (snap / "branch" / f"{case['branch_table']}.json").read_text(encoding="utf-8") == '{"虚构": 1}'

    # source_id 只进快照，不进分析流程（提示词须与 eval 回放时一致）
    stored = asyncio.run(case_files(run_id, parsed["filename"], "lawyer-a"))
    assert stored and "source_id" not in stored[0]

    for dirpath, _dirs, names in os.walk(snap):
        assert os.stat(dirpath).st_mode & 0o777 == 0o700, dirpath
        for name in names:
            assert os.stat(os.path.join(dirpath, name)).st_mode & 0o777 == 0o600, name


def test_other_user_cannot_overwrite_snapshot(snap, monkeypatch):
    run_id, final = _flow(monkeypatch, [{"filename": "审批表.pdf", "text": "审批表 1000 600 300"}])
    before = json.dumps(case_snapshot.load(run_id), ensure_ascii=False)
    case_snapshot.finish(run_id, None, {"defendant_name": {"value": "篡改"}}, "lawyer-b")
    case_snapshot.finish("../../etc", None, {}, "lawyer-a")  # 非法 run_id 直接忽略
    assert json.dumps(case_snapshot.load(run_id), ensure_ascii=False) == before


def test_disabled_writes_nothing(snap, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_ENABLED", False)
    parsed = _upload(_pdf_bytes("Shenpibiao"))
    assert parsed["source_id"] is None
    _flow(monkeypatch, [{"filename": "审批表.pdf", "text": "审批表 1000 600 300"}])
    assert not snap.exists()


def test_source_id_must_be_sha256():
    FileInput(filename="a.pdf", text="x", source_id="a" * 64)
    with pytest.raises(ValidationError):
        FileInput(filename="a.pdf", text="x", source_id="../../etc/passwd")


def test_snapshot_failure_never_breaks_flow(snap, monkeypatch):
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("不是目录")  # 快照目录建不出来
    parsed = _upload(_pdf_bytes("Shenpibiao"))
    assert parsed["source_id"] is None
    run_id, _ = _flow(monkeypatch, [{"filename": "审批表.pdf", "text": "审批表 1000 600 300"}])
    assert run_id


def test_purge_keeps_referenced_materials_fresh(snap, monkeypatch):
    content = _pdf_bytes("Shenpibiao")
    parsed = _upload(content)
    material = snap / "materials" / parsed["source_id"]
    long_ago = time.time() - 170 * 86400
    os.utime(material, (long_ago, long_ago))  # 原件早于案件上传
    run_id, _ = _flow(monkeypatch, [{**parsed, "identified_type": "审批表"}])
    assert os.path.getmtime(material) > long_ago  # 导出时随案件续期
    old_case = snap / "cases" / run_id / "case.json"
    expired = time.time() - 181 * 86400
    for path in (old_case, material, *(snap / "branch").iterdir()):
        os.utime(path, (expired, expired))
    assert case_snapshot.purge_old() == 3
    assert not (snap / "cases" / run_id).exists() and not material.exists()


def test_failed_resume_not_recorded_until_retry_succeeds(snap, monkeypatch):
    # 恢复失败、律师重试：快照里只留成功那一轮（#159 评审）
    from app.agent.graph import get_graph

    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[_FIELDS, "说明\n---HIGHLIGHT---\n- 1"]))

    async def go():
        run_log.set_user("lawyer-a")
        state = await run_analyze([{"filename": "审批表.pdf", "text": "审批表 1000 600 300"}], True, "lawyer-a")
        graph = await get_graph()
        real = graph.ainvoke
        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("恢复失败")
            return await real(*args, **kwargs)

        monkeypatch.setattr(graph, "ainvoke", flaky)
        with pytest.raises(RuntimeError):
            await run_resume(state.run_id, {"total_amount": 800.0}, "lawyer-a")
        assert case_snapshot.load(state.run_id)["decisions"] == []
        await run_resume(state.run_id, {"total_amount": 900.0}, "lawyer-a")
        return state.run_id

    run_id = asyncio.run(go())
    assert [d["answers"] for d in case_snapshot.load(run_id)["decisions"]] == [{"total_amount": 900.0}]


def test_code_version_falls_back_to_git(monkeypatch):
    monkeypatch.setattr(config, "APP_VERSION", None)
    monkeypatch.setattr(case_snapshot, "_git_version", None)
    version = case_snapshot.code_version()
    assert version == "unknown" or len(version) == 40
