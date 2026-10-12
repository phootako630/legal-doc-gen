# /api/branches：查看不限，上传 / 保存 / 回退须管理员口令（数据均为虚构）
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import config
from app.main import app

GOOD = "91440000MA00000015"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BRANCH_INFO_PATH", str(tmp_path / "t.json"))
    monkeypatch.setattr(config, "BRANCH_HISTORY_DIR", str(tmp_path / "hist"))
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret-口令")
    return TestClient(app)


def _xlsx(rep: str) -> bytes:
    book = Workbook()
    book.active.append(["原告名称", "统一社会信用代码", "住所地", "负责人"])
    book.active.append(["甲公司", GOOD, "某地址", rep])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _admin(token: str = "secret-口令") -> dict[str, str]:
    # 请求头只能是 latin-1：前端按 UTF-8 编码后以 encodeURIComponent 形式发送
    from urllib.parse import quote

    return {"X-Admin-Token": quote(token)}


def test_view_is_open_and_empty_table_is_stale(client):
    body = client.get("/api/branches").json()
    assert body["entries"] == [] and body["stale"] is True
    assert client.get("/api/branches/export").status_code == 404


def test_update_requires_admin(client, monkeypatch):
    files = {"file": ("t.xlsx", _xlsx("张"))}
    assert client.post("/api/branches/preview", files=files).status_code == 401
    assert (
        client.post(
            "/api/branches/preview", files=files, headers=_admin("错")
        ).status_code
        == 401
    )
    assert client.post("/api/branches", json={"entries": []}).status_code == 401
    assert (
        client.post("/api/branches/restore", json={"version_id": "1"}).status_code
        == 401
    )
    monkeypatch.setattr(config, "ADMIN_TOKEN", None)
    assert client.post("/api/branches/admin-check", headers=_admin()).status_code == 403


def test_preview_then_save_then_restore(client):
    assert client.post("/api/branches/admin-check", headers=_admin()).json() == {
        "ok": True
    }

    prev = client.post(
        "/api/branches/preview",
        files={"file": ("v1.xlsx", _xlsx("张"))},
        headers=_admin(),
    ).json()
    assert prev["errors"] == [] and prev["diff"][0]["kind"] == "added"
    assert client.get("/api/branches").json()["entries"] == []  # 预览不保存

    saved = client.post(
        "/api/branches",
        json={"entries": prev["entries"], "source_file": "v1.xlsx"},
        headers=_admin(),
    ).json()
    assert saved["entries"][0]["representative"] == "张" and saved["stale"] is False

    prev2 = client.post(
        "/api/branches/preview",
        files={"file": ("v2.xlsx", _xlsx("王"))},
        headers=_admin(),
    ).json()
    assert prev2["diff"][0]["changes"][0]["new"] == "王"
    client.post("/api/branches", json={"entries": prev2["entries"]}, headers=_admin())

    history = client.get("/api/branches/history").json()
    assert len(history) == 1
    restored = client.post(
        "/api/branches/restore", json={"version_id": history[0]["id"]}, headers=_admin()
    ).json()
    assert restored["entries"][0]["representative"] == "张"

    export = client.get("/api/branches/export")
    assert export.status_code == 200 and export.content[:2] == b"PK"


def test_save_rejects_duplicates(client):
    # 同名但内容不同：不知道该留哪一行，拒绝保存
    dup = [{"name": "甲公司", "address": "甲地"}, {"name": "甲公司", "address": "乙地"}]
    res = client.post("/api/branches", json={"entries": dup}, headers=_admin())
    assert res.status_code == 400 and "重复" in res.json()["detail"]


def test_save_merges_identical_duplicates(client):
    # 同一行抄了两遍（律师表里南通分公司出现两次）：自动合并，不阻止保存
    row = {"name": "甲公司", "address": "甲地"}
    res = client.post("/api/branches", json={"entries": [row, row]}, headers=_admin())
    assert res.status_code == 200
    assert [e["name"] for e in res.json()["entries"]] == ["甲公司"]


def test_save_and_restore_record_uploader(client):
    # nginx 认证后把登录账号放在 X-Remote-User；保存与恢复都记入该版本
    files = {"file": ("t.xlsx", _xlsx("张"))}
    entries = client.post(
        "/api/branches/preview", files=files, headers=_admin()
    ).json()["entries"]
    saved = client.post(
        "/api/branches",
        json={"entries": entries, "source_file": "t.xlsx"},
        headers={**_admin(), "X-Remote-User": "lawyer-a"},
    ).json()
    assert saved["uploaded_by"] == "lawyer-a"
    assert client.get("/api/branches").json()["uploaded_by"] == "lawyer-a"

    entries2 = client.post(
        "/api/branches/preview",
        files={"file": ("t2.xlsx", _xlsx("李"))},
        headers=_admin(),
    ).json()["entries"]
    client.post(
        "/api/branches",
        json={"entries": entries2, "source_file": "t2.xlsx"},
        headers={**_admin(), "X-Remote-User": "lawyer-b"},
    )
    history = client.get("/api/branches/history").json()
    assert history[0]["uploaded_by"] == "lawyer-a"  # 被替换下来的旧版本保留原上传人

    restored = client.post(
        "/api/branches/restore",
        json={"version_id": history[0]["id"]},
        headers={**_admin(), "X-Remote-User": "lawyer-b"},
    ).json()
    assert restored["uploaded_by"] == "lawyer-b"  # 恢复操作记执行人


@pytest.mark.parametrize("header", [None, "", "has space", "x" * 33, "a;b"])
def test_missing_or_malformed_uploader_is_blank(client, header):
    # 本地开发没有 nginx，或请求头格式不对：不记上传人，也不影响保存
    headers = _admin()
    if header is not None:
        headers["X-Remote-User"] = header
    res = client.post(
        "/api/branches",
        json={"entries": [{"name": "甲公司", "credit_code": GOOD}]},
        headers=headers,
    )
    assert res.status_code == 200 and res.json()["uploaded_by"] is None
