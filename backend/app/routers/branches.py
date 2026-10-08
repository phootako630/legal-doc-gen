# /api/branches：原告信息表的查看、下载（所有人）与上传更新、回退（仅管理员）。
#
# 更新分两步，防止传错文件把整张表覆盖：
#   1. POST /branches/preview 上传 Excel → 返回解析结果、校验问题、与当前版本的变更；
#   2. 管理员确认后 POST /branches 提交条目 → 服务端重新规范化与校验后保存（旧版存档）。
# 管理员身份凭请求头 X-Admin-Token 与服务器 ADMIN_TOKEN 比对（系统暂无账号体系）。
import hmac
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app import config
from app.services import branch_table as bt

router = APIRouter()

_MAX_TABLE_BYTES = 5 * 1024 * 1024


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """校验管理员口令；未配置口令时一律拒绝更新。

    请求头只能是 latin-1，前端把口令 encodeURIComponent 后发送，这里先解码再比对。
    """
    expected = config.ADMIN_TOKEN
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="服务器未设置管理员口令（ADMIN_TOKEN），暂不能更新原告信息表",
        )
    given = unquote(x_admin_token or "")
    if not given or not hmac.compare_digest(
        given.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="管理员口令不正确")


class BranchEntry(BaseModel):
    name: str
    credit_code: str = ""
    address: str = ""
    representative: str = ""
    phone: str = ""


class BranchTableResponse(BaseModel):
    entries: list[BranchEntry]
    updated_at: str | None
    source_file: str | None
    age_days: int | None
    stale: bool
    stale_days: int
    warnings: list[str]


class DiffChange(BaseModel):
    field: str
    label: str
    old: str
    new: str


class DiffItem(BaseModel):
    name: str
    kind: str  # added | removed | changed
    changes: list[DiffChange]


class PreviewResponse(BaseModel):
    source_file: str
    entries: list[BranchEntry]
    errors: list[str]
    warnings: list[str]
    diff: list[DiffItem]


class SaveRequest(BaseModel):
    entries: list[BranchEntry]
    source_file: str | None = None


class RestoreRequest(BaseModel):
    version_id: str


class HistoryItem(BaseModel):
    id: str
    updated_at: str | None
    source_file: str | None
    count: int


def _table_response(table: bt.BranchTable | None) -> BranchTableResponse:
    if table is None:
        return BranchTableResponse(
            entries=[],
            updated_at=None,
            source_file=None,
            age_days=None,
            stale=True,
            stale_days=config.BRANCH_TABLE_STALE_DAYS,
            warnings=[],
        )
    age = bt.age_days(table)
    return BranchTableResponse(
        entries=[BranchEntry(**e) for e in table.entries],
        updated_at=table.updated_at,
        source_file=table.source_file,
        age_days=age,
        stale=age is None or age > config.BRANCH_TABLE_STALE_DAYS,
        stale_days=config.BRANCH_TABLE_STALE_DAYS,
        warnings=bt.check_entries(table.entries).warnings,
    )


@router.get("/branches", response_model=BranchTableResponse)
async def get_branches() -> BranchTableResponse:
    """当前生效的原告信息表及更新时间（所有人可看）。"""
    return _table_response(bt.load_table(config.BRANCH_INFO_PATH))


@router.get("/branches/export")
async def export_branches() -> Response:
    """下载当前表为 .xlsx（所有人可下载；表头与上传格式一致）。"""
    table = bt.load_table(config.BRANCH_INFO_PATH)
    if table is None:
        raise HTTPException(status_code=404, detail="尚未上传原告信息表")
    date = (table.updated_at or "")[:10]
    filename = f"原告信息表{('_' + date) if date else ''}.xlsx"
    return Response(
        content=bt.to_xlsx(table.entries),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/branches/history", response_model=list[HistoryItem])
async def get_history() -> list[HistoryItem]:
    """历史版本列表（新 → 旧）。"""
    return [HistoryItem(**h) for h in bt.list_history(config.BRANCH_HISTORY_DIR)]


@router.post("/branches/admin-check", dependencies=[Depends(require_admin)])
async def admin_check() -> dict[str, bool]:
    """前端登录时核对管理员口令。"""
    return {"ok": True}


@router.post(
    "/branches/preview",
    response_model=PreviewResponse,
    dependencies=[Depends(require_admin)],
)
async def preview_branches(file: UploadFile = File(...)) -> PreviewResponse:
    """解析上传的 Excel，返回校验问题与相对当前版本的变更；此步不保存。"""
    content = await file.read()
    if len(content) > _MAX_TABLE_BYTES:
        raise HTTPException(status_code=400, detail="表格文件过大（上限 5MB）")
    filename = file.filename or "原告信息表"
    try:
        entries = bt.parse_table(filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    entries, merged = bt.dedupe_entries(entries)
    check = bt.check_entries(entries)
    current = bt.load_table(config.BRANCH_INFO_PATH)
    diff = bt.diff_entries(current.entries if current else [], entries)
    return PreviewResponse(
        source_file=filename,
        entries=[BranchEntry(**e) for e in entries],
        errors=check.errors,
        warnings=merged + check.warnings,
        diff=[DiffItem(**d) for d in diff],  # type: ignore[arg-type]
    )


@router.post(
    "/branches",
    response_model=BranchTableResponse,
    dependencies=[Depends(require_admin)],
)
async def save_branches(req: SaveRequest) -> BranchTableResponse:
    """保存为当前表（服务端重新规范化与校验；有错误拒绝保存）。"""
    entries, _ = bt.dedupe_entries(
        [bt.normalize_entry(e.model_dump()) for e in req.entries]
    )
    check = bt.check_entries(entries)
    if check.errors:
        raise HTTPException(status_code=400, detail="；".join(check.errors))
    table = bt.save_table(
        entries, req.source_file, config.BRANCH_INFO_PATH, config.BRANCH_HISTORY_DIR
    )
    return _table_response(table)


@router.post(
    "/branches/restore",
    response_model=BranchTableResponse,
    dependencies=[Depends(require_admin)],
)
async def restore_branches(req: RestoreRequest) -> BranchTableResponse:
    """把某个历史版本恢复为当前表。"""
    try:
        table = bt.restore_table(
            req.version_id, config.BRANCH_INFO_PATH, config.BRANCH_HISTORY_DIR
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _table_response(table)
