# POST /api/client-error：前端上报未捕获异常与接口 5xx（#76）
#
# 只收固定枚举（错误类型、所在步骤、哪个接口）、HTTP 状态码和 run_id，不收错误消息原文
# （浏览器的报错消息里可能带字段取值）；多余字段直接拒绝（extra=forbid），原文塞不进来。
# 写入运行日志 client_error 事件，按账号 / IP 限流，免得前端报错循环把日志刷满。
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.routers.auth import client_ip
from app.services import run_log
from app.services.rate_limit import SlidingWindowLimiter

router = APIRouter()

ClientErrorKind = Literal["uncaught", "unhandled_rejection", "api_5xx"]
ClientStep = Literal["upload", "processing", "review", "preview", "other"]
ClientApi = Literal[
    "upload", "analyze", "analyze_result", "resume", "case", "generate", "feedback", "extract",
    "progress", "branches", "auth", "other",
]

limiter = SlidingWindowLimiter(limit=config.CLIENT_ERROR_RATE_PER_MIN, window_s=60)


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ClientErrorKind
    step: ClientStep
    # 会话编号固定是 uuid4().hex（32 位小写十六进制）：再宽一点就能夹带任意文字进日志（#104 评审）
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    # 只在 api_5xx 时有意义
    api: ClientApi | None = None
    status: int | None = Field(default=None, ge=500, le=599)


@router.post("/client-error", status_code=204)
async def client_error(report: ClientErrorReport, request: Request) -> Response:
    key = run_log.current_user() or client_ip(request)
    if not limiter.allow(key):
        raise HTTPException(status_code=429, detail="上报过于频繁")
    run_log.log_event("client_error", run_id=report.run_id, **report.model_dump(exclude={"run_id"}, exclude_none=True))
    return Response(status_code=204)
