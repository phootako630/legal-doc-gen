# 同名材料自动编号（#121）。虚构文件名。
import io

from fastapi.testclient import TestClient

from app.services.file_names import renamed_notes, unique_filenames


def test_duplicates_numbered_first_kept():
    names, renamed = unique_filenames(["安装合同.pdf", "审批表.pdf", "安装合同.pdf", "安装合同.pdf"])
    assert names == ["安装合同.pdf", "审批表.pdf", "安装合同(2).pdf", "安装合同(3).pdf"]
    assert renamed == [("安装合同.pdf", "安装合同(2).pdf"), ("安装合同.pdf", "安装合同(3).pdf")]
    assert "改称「安装合同(2).pdf」" in renamed_notes(renamed)[0]


def test_new_name_avoids_existing_names():
    names, _ = unique_filenames(["合同.pdf", "合同(2).pdf", "合同.pdf"])
    assert names == ["合同.pdf", "合同(2).pdf", "合同(3).pdf"]


def test_unique_names_untouched():
    assert unique_filenames(["a.pdf", "b.docx"]) == (["a.pdf", "b.docx"], [])


def test_upload_response_numbers_duplicates(monkeypatch):
    from app import config
    from app.main import app
    from app.routers import files as files_router

    monkeypatch.setattr(config, "AUTH_ENABLED", False)

    async def fake_parse(upload):
        return {"filename": upload.filename, "identified_type": "合同", "text": "虚构", "is_scanned": False,
                "page_count": 1}

    monkeypatch.setattr(files_router, "parse_file", fake_parse)
    res = TestClient(app).post("/api/upload", files=[
        ("files", ("虚构合同.pdf", io.BytesIO(b"a"), "application/pdf")),
        ("files", ("虚构合同.pdf", io.BytesIO(b"bb"), "application/pdf")),
    ])
    body = res.json()
    assert [f["filename"] for f in body["files"]] == ["虚构合同.pdf", "虚构合同(2).pdf"]
    assert len(body["renamed"]) == 1 and "虚构合同(2).pdf" in body["renamed"][0]
