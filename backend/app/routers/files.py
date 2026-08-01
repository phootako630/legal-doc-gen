# POST /api/upload：接收上传文件，调度解析（PDF/Word/OCR），返回每份文件的文本和元信息
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.services.file_parser import parse_file

router = APIRouter()


class ParsedFile(BaseModel):
    filename: str
    identified_type: str  # '审批表' | '合同' | '验收报告' | '未知'
    text: str
    is_scanned: bool
    page_count: int


class UploadResponse(BaseModel):
    files: list[ParsedFile]
    can_proceed: bool
    missing_materials: list[str]
    warnings: list[str]


@router.post("/upload", response_model=UploadResponse)
async def upload(files: list[UploadFile] = File(...)) -> UploadResponse:
    """接收多个文件，解析后返回文本内容和类型识别结果。"""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    parsed: list[ParsedFile] = []
    warnings: list[str] = []

    for upload_file in files:
        try:
            result = await parse_file(upload_file)
            parsed.append(ParsedFile(**result))
        except Exception as e:
            warnings.append(f"{upload_file.filename}：解析失败 — {e}")

    identified_types = {f.identified_type for f in parsed}

    # 收集所有缺失类型，供前端展示参考
    missing: list[str] = []
    for doc_type in ["审批表", "合同", "验收报告"]:
        if doc_type not in identified_types:
            missing.append(doc_type)

    # 流程继续条件：至少有审批表 + 合同（验收报告缺失不阻断，仅列入 missing_materials）
    required_present = {"审批表", "合同"}.issubset(identified_types)
    can_proceed = len(parsed) > 0 and required_present

    return UploadResponse(
        files=parsed,
        can_proceed=can_proceed,
        missing_materials=missing,
        warnings=warnings,
    )
