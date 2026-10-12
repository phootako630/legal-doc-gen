# FastAPI 应用入口：注册路由、配置 CORS
import sys

# print() 默认走 sys.stdout 的编码；输出被重定向/管道时（如 uvicorn 日志写入文件），
# Windows 上会退化成系统代码页（cp1252），届时任何 print() 里出现中文都会抛
# UnicodeEncodeError 并使当次请求变成 500——必须在其他模块 import 之前重设为 utf-8，
# 否则 config.py / routers 里模块加载期或请求处理期打印的中文日志都会踩到这个坑
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 以下 import 必须在上面的 stdout 重设之后，故豁免 E402
import asyncio  # noqa: E402
from contextlib import asynccontextmanager, suppress  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.agent.graph import get_graph, reset_graph  # noqa: E402
from app.agent.runner import retention_loop  # noqa: E402
from app import config  # noqa: E402
from app.config import CORS_ORIGINS  # noqa: E402
from app.services import auth_store  # noqa: E402
from app.routers import (  # noqa: E402
    analyze,
    auth,
    branches,
    extract,
    feedback,
    files,
    generate,
    resume,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时建好 checkpoint 库（尽早暴露路径/权限问题）并开始定时清理过期案件数据，关闭时收尾。"""
    if config.AUTH_ENABLED:
        users = await asyncio.to_thread(auth_store.list_users)  # 顺带建好账号库
        if not users:
            print("[AUTH] 已开启登录，但还没有任何账号：用 scripts/manage_users.py add 创建", flush=True)
    else:
        print("[AUTH] 未开启登录（AUTH_ENABLED!=1）：所有接口无需登录，仅限本地开发", flush=True)
    await get_graph()
    purge_task = asyncio.create_task(retention_loop())
    yield
    purge_task.cancel()
    with suppress(asyncio.CancelledError):
        await purge_task
    await reset_graph()


app = FastAPI(title="安装合同纠纷起诉状生成系统", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 登录校验：除登录相关接口外，所有 /api 都要求已登录（AUTH_ENABLED 关闭时放行）
app.middleware("http")(auth.auth_middleware)

app.include_router(auth.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(extract.router, prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(resume.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(branches.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")



@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "服务运行正常"}
