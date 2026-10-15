# /api/auth：登录、退出、当前账号、修改密码；以及保护其余 /api 接口的登录校验中间件。
#
# 会话令牌放在 HttpOnly + SameSite=Strict 的 Cookie 里：网页脚本读不到，跨站请求也带不上
# （顺带挡住 CSRF）。走 HTTPS 时加 Secure；本地 / IP+HTTP 验证时浏览器会拒收 Secure Cookie，
# 所以按请求实际协议决定（nginx 用 X-Forwarded-Proto 告知）。
# 账号在服务器上用 scripts/manage_users.py 创建，没有注册入口。
import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.services import auth_store, run_log
from app.services.login_guard import guard

router = APIRouter()

COOKIE_NAME = "session"
# 未登录也能访问的接口：登录本身、查询登录状态（前端据此决定显示登录页）、退出
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/me", "/api/auth/logout"}
# 前端凭这个响应头区分「需要登录」与其他 401（如原告信息表的管理员口令错误）
LOGIN_REQUIRED_HEADER = "X-Login-Required"


def client_ip(request: Request) -> str:
    """后端不对外，只经 nginx 访问：nginx 写的 X-Real-IP 可信；本地开发退回直连地址。"""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "-")


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.lower() == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True,
        secure=_is_https(request),
        samesite="strict",
        path="/",
    )


async def auth_middleware(request: Request, call_next):
    """未登录访问受保护的 /api 接口一律 401；已登录把账号放进 request.state.user。"""
    request.state.user = None
    if not config.AUTH_ENABLED:
        return await call_next(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        request.state.user = await asyncio.to_thread(auth_store.session_user, token)
    # 运行日志带上账号名（本请求及其启动的后台分析）
    run_log.set_user(request.state.user)
    path = request.url.path
    if path.startswith("/api/") and path not in _PUBLIC_PATHS and request.state.user is None:
        return JSONResponse(
            {"detail": "请先登录"}, status_code=401, headers={LOGIN_REQUIRED_HEADER: "1"}
        )
    return await call_next(request)


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class MeResponse(BaseModel):
    auth_enabled: bool
    username: str | None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


# 以下接口用同步函数：scrypt 校验要几十毫秒，FastAPI 会放到线程池里跑，不卡事件循环


@router.post("/auth/login", response_model=MeResponse)
def login(req: LoginRequest, request: Request, response: Response) -> MeResponse:
    if not config.AUTH_ENABLED:
        return MeResponse(auth_enabled=False, username=None)
    username = req.username.strip()
    ip = client_ip(request)
    wait = guard.locked_seconds(ip, username)
    if wait:
        minutes = (wait + 59) // 60
        raise HTTPException(status_code=429, detail=f"输错次数过多，请 {minutes} 分钟后再试")
    if not auth_store.check_password(username, req.password):
        guard.record_failure(ip, username)
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    guard.record_success(ip, username)
    _set_cookie(response, request, auth_store.create_session(username))
    return MeResponse(auth_enabled=True, username=username)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    auth_store.delete_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/auth/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    return MeResponse(auth_enabled=config.AUTH_ENABLED, username=request.state.user)


@router.post("/auth/change-password", status_code=204)
def change_password(req: ChangePasswordRequest, request: Request) -> None:
    username: str | None = request.state.user
    if username is None:  # 未开启登录时没有账号可改
        raise HTTPException(status_code=400, detail="当前未启用登录，无需修改密码")
    ip = client_ip(request)
    wait = guard.locked_seconds(ip, username)
    if wait:
        minutes = (wait + 59) // 60
        raise HTTPException(status_code=429, detail=f"输错次数过多，请 {minutes} 分钟后再试")
    if not auth_store.check_password(username, req.old_password):
        guard.record_failure(ip, username)
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if req.new_password == req.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    try:
        # 当前这台电脑保持登录，其他地方登录的会话全部失效
        auth_store.set_password(username, req.new_password, keep_token=request.cookies.get(COOKIE_NAME))
    except auth_store.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    guard.record_success(ip, username)
