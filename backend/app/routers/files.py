# POST /api/upload：接收上传文件，调度解析（PDF/Word/OCR），返回每份文件的文本和元信息
# GET /api/upload/progress?progress_id=<id>：该次上传的实时进度，供前端轮询渲染进度条
import logging

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.services import upload_progress
from app.services.file_parser import DocType, parse_file
from app.services.materials import material_status

logger = logging.getLogger(__name__)

router = APIRouter()


class PageText(BaseModel):
    page: int  # 页码，从 1 开始
    text: str  # 该页文本（可解析 PDF 为 PyMuPDF 逐页文本；扫描件为 OCR 逐页结果）


class ParsedFile(BaseModel):
    filename: str
    identified_type: DocType  # 系统识别的类型；上传页律师可改
    text: str
    is_scanned: bool
    page_count: int
    # 逐页文本，供抽取值回原文定位到具体页码；旧客户端可不带，缺省空列表
    pages: list[PageText] = []
    # 扫描件暂存字节的引用，供 agent 按需逐页 OCR；非扫描件为 None
    file_id: str | None = None


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
async def upload(
    files: list[UploadFile] = File(...),
    # 前端生成的进度 id：多人同时上传时各自的进度互不干扰；不带则不记录进度
    progress_id: str | None = Form(default=None, max_length=128),
) -> UploadResponse:
    """接收多个文件，解析后返回文本内容和类型识别结果。"""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    parsed: list[ParsedFile] = []
    warnings: list[str] = []

    with upload_progress.track(progress_id, total_files=len(files)):
        for idx, upload_file in enumerate(files, start=1):
            upload_progress.start_file(upload_file.filename or "未知文件", idx)
            try:
                result = await parse_file(upload_file)
                parsed.append(ParsedFile(**result))
            except Exception as e:
                # 解析失败（含 OCR 失败）在旧版本里只进了 warnings、从不打印，
                # 排查时后端日志完全看不出原因——必须打印，否则只能靠猜
                # 不记文件名与异常原文：案卷名常含当事人名称，异常里可能带原文（#84 评审）
                logger.warning("第 %d 份文件解析失败：%s", idx, type(e).__name__)
                warnings.append(f"{upload_file.filename}：解析失败 — {e}")

    # 缺哪些材料、能否直接开始分析（与前端 lib/materials.ts、agent 清点节点同一口径）
    status = material_status(f.identified_type for f in parsed)

    return UploadResponse(
        files=parsed,
        can_proceed=status.can_proceed,
        missing_materials=status.missing,
        warnings=warnings,
    )


@router.get("/upload/progress", response_model=UploadProgress)
async def get_upload_progress(
    progress_id: str | None = Query(default=None, max_length=128),
) -> UploadProgress:
    """返回指定上传任务的处理进度；未知或已结束的任务返回非活跃状态。"""
    return UploadProgress(**upload_progress.snapshot(progress_id))
