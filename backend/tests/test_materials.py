# 材料齐全判定（#69）：纯函数，与 /api/upload、前端 lib/materials.ts 同一口径；文件名均为虚构
from app.services.materials import material_note, material_status


def test_complete():
    s = material_status(["审批表", "合同", "验收报告", "验收报告"])
    assert s.can_proceed and s.missing == []
    assert material_note(s) == ""


def test_missing_acceptance_can_proceed_with_reminder():
    s = material_status(["审批表", "合同"])
    assert s.can_proceed and s.missing == ["验收报告"]
    assert material_note(s) == "未上传验收报告，相关字段可能缺失，请律师补充。"


def test_missing_required_cannot_proceed():
    for types, missing in (
        (["合同", "验收报告"], ["审批表"]),
        (["审批表", "补充协议"], ["合同", "验收报告"]),
    ):
        s = material_status(types)
        assert not s.can_proceed and s.missing == missing
        assert "律师已选择按现有材料继续分析" in material_note(s)


def test_lawyer_changed_unknown_to_contract():
    # 上传页律师把认不出的扫描件从「未知」改成「合同」：按改后的类型判定
    before = [{"identified_type": "审批表"}, {"identified_type": "未知"}]
    after = [{"identified_type": "审批表"}, {"identified_type": "合同"}]
    assert not material_status(f["identified_type"] for f in before).can_proceed
    assert material_status(f["identified_type"] for f in after).can_proceed


def test_no_files():
    s = material_status([])
    assert not s.can_proceed and s.missing == ["审批表", "合同", "验收报告"]


def test_upload_endpoint_uses_same_rule(monkeypatch):
    # /api/upload 的 can_proceed / missing_materials 与纯函数一致
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import files as files_router

    kinds = iter(["审批表", "验收报告"])

    async def fake_parse(upload):
        return {
            "filename": upload.filename,
            "identified_type": next(kinds),
            "text": "虚构正文",
            "is_scanned": False,
            "page_count": 1,
        }

    monkeypatch.setattr(files_router, "parse_file", fake_parse)
    res = TestClient(app).post(
        "/api/upload",
        files=[
            ("files", ("虚构审批表.pdf", b"x", "application/pdf")),
            ("files", ("虚构监检报告.pdf", b"x", "application/pdf")),
        ],
    )
    body = res.json()
    assert res.status_code == 200
    assert body["can_proceed"] is False and body["missing_materials"] == ["合同"]
