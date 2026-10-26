# GET /api/case/{run_id}/page：审核页「查看原件第 N 页」，返回扫描件某一页的 PNG（#111）
#
# 原件是客户的合同：只有发起分析的账号能看（别人的、过期的会话一律 404，不透露是否存在），
# 文件只在本会话的材料里按文件名找（file_id 不经客户端），响应禁止任何缓存。
# 文字版材料没有暂存原件，返回明确提示，律师直接打开原文件核对。
# 运行日志只记字段 key、页码和结果，不记文件名（文件名常含公司名）。
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.agent.runner import case_file
from app.services import file_store, run_log
from app.services.page_image import render_page_png

router = APIRouter()

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NOT_FOUND = "原件不存在或会话已过期，请重新上传材料"
TEXT_ONLY = "这份材料是文字版，没有暂存原件图片，请打开原文件核对"
_NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


@router.get("/case/{run_id}/page")
async def source_page(
    run_id: str,
    file: str = Query(..., min_length=1, max_length=255),
    page: int = Query(..., ge=1, le=10000),
    field: str | None = Query(default=None, max_length=64),
) -> Response:
    """会话材料 file 第 page 页的 PNG；field 是律师从哪个字段点开的，只用于统计。"""
    field_key = field if field and _FIELD_RE.fullmatch(field) else None

    def fail(status: int, detail: str, outcome: str) -> HTTPException:
        run_log.log_event("source_page_view", run_id=run_id if _RUN_ID_RE.fullmatch(run_id) else None,
                          field=field_key, page=page, outcome=outcome)
        return HTTPException(status_code=status, detail=detail, headers=_NO_STORE)

    if not _RUN_ID_RE.fullmatch(run_id):
        raise fail(404, NOT_FOUND, "not_found")
    f = await case_file(run_id, file, run_log.current_user())
    if f is None:
        raise fail(404, NOT_FOUND, "not_found")
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
