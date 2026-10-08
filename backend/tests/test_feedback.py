# 律师反馈单测：保存进案件库、运行日志不含说明文字、未知/过期会话拒绝、随案件过期清除
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import config
from app.agent import checkpoint
from app.agent import nodes as nodes_mod
from app.agent.runner import run_analyze
from app.routers.feedback import FeedbackRequest, feedback
from app.services import run_log

_COMMENT = "被告名称少了「有限」二字，审批表第2页写的是某地产有限公司"


def _analyze_once(monkeypatch) -> str:
    monkeypatch.setattr(
        nodes_mod,
        "chat",
        AsyncMock(
            side_effect=[
                {"can_proceed": True, "missing": [], "notes": ""},
                {"defendant_name": {"value": "某地产公司", "src": "《审批表.pdf》"}},
                "说明\n---HIGHLIGHT---\n",
            ]
        ),
    )

    async def go():
        case = await run_analyze([{"filename": "审批表.pdf", "text": "某地产公司"}], True)
        return case.run_id

    return asyncio.run(go())


def test_feedback_saved_and_log_has_no_comment_text(monkeypatch, tmp_path):
    log_path = tmp_path / "run_log.jsonl"
    monkeypatch.setattr(run_log, "RUN_LOG_PATH", str(log_path))
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    run_id = _analyze_once(monkeypatch)

    async def go():
        resp = await feedback(
            FeedbackRequest(
                run_id=run_id,
                field_key="defendant_name",
                category="wrong_value",
                comment=_COMMENT,
            )
        )
        return resp, await checkpoint.list_feedback(run_id)

    resp, rows = asyncio.run(go())
    assert resp.ok is True
    assert len(rows) == 1
    assert rows[0]["field_key"] == "defendant_name"
    assert rows[0]["category"] == "wrong_value"
    assert rows[0]["comment"] == _COMMENT

    # 运行日志只有 key / 分类 / 有无说明，绝不含说明文字（保密约束）
    log_text = log_path.read_text(encoding="utf-8")
    fb = [json.loads(line) for line in log_text.splitlines() if '"feedback"' in line]
    assert fb and fb[-1]["field_key"] == "defendant_name"
    assert fb[-1]["category"] == "wrong_value"
    assert fb[-1]["has_comment"] is True
    assert _COMMENT not in log_text
    assert "有限" not in log_text


def test_unknown_run_is_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(feedback(FeedbackRequest(run_id="nosuchrun01", category="other")))
    assert exc.value.status_code == 404


def test_expired_run_is_rejected(monkeypatch):
    run_id = _analyze_once(monkeypatch)
    monkeypatch.setattr(config, "CASE_RETENTION_DAYS", -1)  # 让刚建的会话立刻算过期
    with pytest.raises(HTTPException) as exc:
        asyncio.run(feedback(FeedbackRequest(run_id=run_id, category="missing")))
    assert exc.value.status_code == 404


def test_feedback_purged_with_case(monkeypatch):
    run_id = _analyze_once(monkeypatch)

    async def go():
        await feedback(FeedbackRequest(run_id=run_id, category="other", comment="x"))
        assert len(await checkpoint.list_feedback(run_id)) == 1
        await checkpoint.purge_expired(retention_days=-1)  # 全部过期
        return await checkpoint.list_feedback(run_id)

    assert asyncio.run(go()) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"run_id": "../../etc", "category": "other"},
        {"run_id": "run12345", "category": "无效分类"},
        {"run_id": "run12345", "category": "other", "field_key": "a b<script>"},
        {"run_id": "run12345", "category": "other", "comment": "长" * 1001},
    ],
)
def test_invalid_requests_rejected(bad):
    with pytest.raises(ValidationError):
        FeedbackRequest(**bad)


def test_list_feedback_script(monkeypatch):
    run_id = _analyze_once(monkeypatch)
    asyncio.run(
        feedback(
            FeedbackRequest(
                run_id=run_id, field_key="defendant_name", category="missing", comment=_COMMENT
            )
        )
    )
    asyncio.run(checkpoint.close())  # 让脚本以只读方式读到落盘数据
    script = Path(__file__).resolve().parent.parent / "scripts" / "list_feedback.py"
    out = subprocess.run(
        [sys.executable, "-I", str(script), config.CHECKPOINT_DB_PATH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "共 1 条反馈" in out
    assert "defendant_name" in out
    assert "缺失 / 没抽到" in out
    assert _COMMENT in out
