# 审核页查看原件（#111）：本人能看扫描件某一页；别人、过期会话、非法编号、不在本会话材料里的
# 文件、同名材料有多份时一律 404；文字版材料给明确提示；响应禁止缓存；文件名不进 URL
# （访问日志会记请求行）；运行日志不记文件名。材料与账号均为虚构。
import asyncio
import json
from unittest.mock import AsyncMock

import fitz
from fastapi.testclient import TestClient

from app import config
from app.agent import nodes as nodes_mod
from app.agent import runner
from app.services import auth_store, file_store, run_log

_SCAN = "虚构梧桐项目安装合同.pdf"
_TEXT = "虚构审批表.pdf"
_FIELDS = {
    "total_amount": {"value": 900000, "src": "《虚构审批表.pdf》"},
    "paid_amount": {"value": 638667.2, "src": "《虚构审批表.pdf》"},
    "unpaid_amount": {"value": 256266.8, "src": "《虚构审批表.pdf》"},
}


def _pdf(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=200, height=200)
    return doc.tobytes()


def _start_case(monkeypatch, owner: str | None = "lawyer01", duplicate: bool = False) -> str:
    monkeypatch.setattr(nodes_mod, "chat", AsyncMock(side_effect=[_FIELDS]))
    scan = {
        "filename": _SCAN,
        "identified_type": "未知",
        "text": "",
        "is_scanned": True,
        "page_count": 2,
    }
    # 同名的第二份扫描件（另一份合同起了同样的名字）
    # 第二份 3 页：请求第 3 页能分出看的是哪一份
    extra = [scan | {"page_count": 3, "file_id": file_store.put(_pdf(3))}] if duplicate else []
    files = [
        {"filename": _TEXT, "identified_type": "审批表", "text": "虚构审批表", "file_id": None},
        # 「未知」类型的扫描件：agent 不会对它按需 OCR，测试不发外部请求
        {
            "filename": _SCAN,
            "identified_type": "未知",
            "text": "",
            "is_scanned": True,
            "page_count": 2,
            "file_id": file_store.put(_pdf(2)),
        },
        *extra,
    ]
    return asyncio.run(runner.run_analyze(files, True, owner=owner)).run_id


def _client(monkeypatch, user: str) -> TestClient:
    monkeypatch.setattr(config, "AUTH_ENABLED", True)
    auth_store.create_user(user, "虚构的足够长密码123")
    from app.main import app

    client = TestClient(app)
    client.cookies.update({"session": auth_store.create_session(user)})
    return client


def _get(client: TestClient, run_id: str, file: str = _SCAN, page: int = 1,
         field: str = "payment_clause_text"):
    return client.post(f"/api/case/{run_id}/page", json={"file": file, "page": page, "field": field})


def test_owner_gets_png_without_caching(monkeypatch):
    run_id = _start_case(monkeypatch)
    res = _get(_client(monkeypatch, "lawyer01"), run_id, page=2)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG")
    assert "no-store" in res.headers["cache-control"]


def test_other_account_gets_404(monkeypatch):
    run_id = _start_case(monkeypatch)
    res = _get(_client(monkeypatch, "lawyer02"), run_id)
    assert res.status_code == 404
    assert "no-store" in res.headers["cache-control"]


def test_expired_case_gets_404(monkeypatch):
    run_id = _start_case(monkeypatch)
    client = _client(monkeypatch, "lawyer01")
    monkeypatch.setattr(config, "CASE_RETENTION_DAYS", -1)
    assert _get(client, run_id).status_code == 404


def test_invalid_ids_get_404(monkeypatch):
    run_id = _start_case(monkeypatch)
    client = _client(monkeypatch, "lawyer01")
    # 非法会话编号、路径穿越式文件名、本会话材料里没有的文件、越界页码
    assert _get(client, "../../etc").status_code == 404
    assert _get(client, "z" * 32).status_code == 404
    assert _get(client, run_id, file="../uploads/" + "a" * 32).status_code == 404
    assert _get(client, run_id, file="别的案件合同.pdf").status_code == 404
    res = _get(client, run_id, page=3)
    assert res.status_code == 404 and "第 3 页" in res.json()["detail"]


def test_text_material_gets_explicit_message(monkeypatch):
    run_id = _start_case(monkeypatch)
    res = _get(_client(monkeypatch, "lawyer01"), run_id, file=_TEXT)
    assert res.status_code == 404
    assert "文字版" in res.json()["detail"]


def test_log_records_field_and_page_but_not_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(run_log, "RUN_LOG_ENABLED", True)
    run_id = _start_case(monkeypatch)
    client = _client(monkeypatch, "lawyer01")
    _get(client, run_id, page=2)
    _get(client, run_id, field="非法 key")
    raw = open(run_log.RUN_LOG_PATH, encoding="utf-8").read()
    views = [json.loads(line) for line in raw.splitlines() if '"source_page_view"' in line]
    assert [(v["field"], v["page"], v["outcome"]) for v in views] == [
        ("payment_clause_text", 2, "ok"),
        (None, 1, "ok"),
    ]
    assert "虚构梧桐" not in raw


def test_same_name_scans_numbered_and_viewable(monkeypatch):
    # 两份同名扫描件：第二份自动改称「…(2).pdf」，按新名称看到的是第二份（#121）
    run_id = _start_case(monkeypatch, duplicate=True)
    client = _client(monkeypatch, "lawyer01")
    second = "虚构梧桐项目安装合同(2).pdf"
    assert _get(client, run_id, file=second, page=3).status_code == 200
    assert _get(client, run_id, page=3).status_code == 404  # 第一份只有 2 页
    assert _get(client, run_id, page=1).status_code == 200


def test_legacy_same_name_session_still_ambiguous(monkeypatch):
    # 升级前的会话里两份材料仍同名：分不清字段出自哪一份，照旧不显示（#118 评审、#121）
    monkeypatch.setattr(runner, "unique_filenames", lambda names: (names, []))
    run_id = _start_case(monkeypatch, duplicate=True)
    res = _get(_client(monkeypatch, "lawyer01"), run_id)
    assert res.status_code == 404
    assert "同名" in res.json()["detail"]


def test_filename_never_in_url(monkeypatch):
    # nginx / uvicorn 访问日志记完整请求行：文件名只能放请求体，GET 查询串不再受理（#118 评审）
    run_id = _start_case(monkeypatch)
    client = _client(monkeypatch, "lawyer01")
    assert client.get(f"/api/case/{run_id}/page?file={_SCAN}&page=1").status_code == 405
