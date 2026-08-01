# POST /api/upload：接收上传文件，调度解析（PDF/Word/OCR），返回每份文件的文本和元信息
# GET /api/upload/progress：上传处理期间的实时进度，供前端轮询渲染进度条
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.services import upload_progress
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


class UploadProgress(BaseModel):
    active: bool
    filename: str | None
    file_index: int
    total_files: int
    stage: str  # '解析中' | 'OCR识别中' | ''
    done_pages: int
    total_pages: int


@router.post("/upload", response_model=UploadResponse)
async def upload(files: list[UploadFile] = File(...)) -> UploadResponse:
    """接收多个文件，解析后返回文本内容和类型识别结果。"""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    parsed: list[ParsedFile] = []
    warnings: list[str] = []

    upload_progress.begin(total_files=len(files))
    try:
        for idx, upload_file in enumerate(files, start=1):
            upload_progress.start_file(upload_file.filename or "未知文件", idx)
            try:
                result = await parse_file(upload_file)
                parsed.append(ParsedFile(**result))
            except Exception as e:
                # 解析失败（含 OCR 失败）在旧版本里只进了 warnings、从不打印，
                # 排查时后端日志完全看不出原因——必须打印，否则只能靠猜
                print(f"[UPLOAD] 文件解析失败：{upload_file.filename} — {e}", flush=True)
                warnings.append(f"{upload_file.filename}：解析失败 — {e}")
    finally:
        upload_progress.finish()

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


@router.get("/upload/progress", response_model=UploadProgress)
async def get_upload_progress() -> UploadProgress:
    """返回当前上传任务的处理进度（v1 单用户全局态，无任务 ID）。"""
    return UploadProgress(**upload_progress.snapshot())
