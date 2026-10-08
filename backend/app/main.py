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
from app.config import CORS_ORIGINS  # noqa: E402
from app.routers import (  # noqa: E402
    analyze,
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
