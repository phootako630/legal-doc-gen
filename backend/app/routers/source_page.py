# POST /api/case/{run_id}/page：审核页「查看原件第 N 页」，返回扫描件某一页的 PNG（#111）
#
# 原件是客户的合同：只有发起分析的账号能看（别人的、过期的会话一律 404，不透露是否存在），
# 文件只在本会话的材料里按文件名找（file_id 不经客户端），响应禁止任何缓存。
# 文件名放请求体、不放查询串：nginx / uvicorn 的访问日志会记下完整请求行（#118 评审）。
# 同名材料有多份时分不清字段出自哪一份，不显示，免得律师核对错原件。
# 文字版材料没有暂存原件，返回明确提示，律师直接打开原文件核对。
# 运行日志只记字段 key、页码和结果，不记文件名（文件名常含公司名）。
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.agent.runner import case_files
from app.services import file_store, run_log
from app.services.page_image import render_page_png

router = APIRouter()

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NOT_FOUND = "原件不存在或会话已过期，请重新上传材料"
TEXT_ONLY = "这份材料是文字版，没有暂存原件图片，请打开原文件核对"
AMBIGUOUS = "有多份同名材料，分不清是哪一份，请打开原文件核对"
_NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


class SourcePageRequest(BaseModel):
    file: str = Field(min_length=1, max_length=255)
    page: int = Field(ge=1, le=10000)
    # 律师从哪个字段点开的，只用于统计
    field: str | None = Field(default=None, max_length=64)


@router.post("/case/{run_id}/page")
async def source_page(run_id: str, req: SourcePageRequest) -> Response:
    """会话材料 req.file 第 req.page 页的 PNG。"""
    page = req.page
    field_key = req.field if req.field and _FIELD_RE.fullmatch(req.field) else None

    def fail(status: int, detail: str, outcome: str) -> HTTPException:
        run_log.log_event("source_page_view", run_id=run_id if _RUN_ID_RE.fullmatch(run_id) else None,
                          field=field_key, page=page, outcome=outcome)
        return HTTPException(status_code=status, detail=detail, headers=_NO_STORE)

    if not _RUN_ID_RE.fullmatch(run_id):
        raise fail(404, NOT_FOUND, "not_found")
    matches = await case_files(run_id, req.file, run_log.current_user())
    if not matches:
        raise fail(404, NOT_FOUND, "not_found")
    if len(matches) > 1:
        raise fail(404, AMBIGUOUS, "ambiguous")
    f = matches[0]
    if not f.get("file_id"):
        raise fail(404, TEXT_ONLY, "text_only")
    pdf = file_store.get(f["file_id"])
    if pdf is None:
        raise fail(404, NOT_FOUND, "not_found")
    png = await render_page_png(pdf, page)
    if png is None:
        raise fail(404, f"这份材料没有第 {page} 页", "no_page")
    run_log.log_event("source_page_view", run_id=run_id, field=field_key, page=page, outcome="ok")
    return Response(content=png, media_type="image/png", headers=_NO_STORE)
