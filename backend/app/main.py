# FastAPI 应用入口：注册路由、配置 CORS
import sys

# print() 默认走 sys.stdout 的编码；输出被重定向/管道时（如 uvicorn 日志写入文件），
# Windows 上会退化成系统代码页（cp1252），届时任何 print() 里出现中文都会抛
# UnicodeEncodeError 并使当次请求变成 500——必须在其他模块 import 之前重设为 utf-8，
# 否则 config.py / routers 里模块加载期或请求处理期打印的中文日志都会踩到这个坑
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import logging  # noqa: E402

# 后端统一用 logging 输出到 stdout（docker 日志驱动按大小切分，见 docker-compose.yml）；
# 先于其他模块 import 配置好，config.py 加载期的提示也能输出
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# 以下 import 必须在上面的 stdout 重设之后，故豁免 E402
import asyncio  # noqa: E402
from contextlib import asynccontextmanager, suppress  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.agent import checkpoint  # noqa: E402
from app.agent.graph import get_graph, reset_graph  # noqa: E402
from app.agent.runner import active_runs, is_draining, retention_loop, start_draining  # noqa: E402
from app import config  # noqa: E402
from app.config import CORS_ORIGINS  # noqa: E402
from app.services import auth_store, daily_summary, file_store, readiness, run_log  # noqa: E402
from app.routers import (  # noqa: E402
    analyze,
    auth,
    branches,
    client_errors,
    extract,
    feedback,
    files,
    generate,
    resume,
    source_page,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时建好 checkpoint 库（尽早暴露路径/权限问题），开始定时清理过期案件数据、
    每天写运行小结，关闭时收尾。"""
    if config.AUTH_ENABLED:
        users = await asyncio.to_thread(auth_store.list_users)  # 顺带建好账号库
        if not users:
            logger.warning("已开启登录，但还没有任何账号：用 scripts/manage_users.py add 创建")
    else:
        logger.warning("未开启登录（AUTH_ENABLED!=1）：所有接口无需登录，仅限本地开发")
    await get_graph()
    tasks = [asyncio.create_task(retention_loop())]
    if config.DAILY_SUMMARY_ENABLED:
        tasks.append(asyncio.create_task(daily_summary.daily_loop()))
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
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
app.include_router(client_errors.router, prefix="/api")
app.include_router(source_page.router, prefix="/api")



# 部署排空（deploy/deploy.sh）：不在 /api 下，nginx 只转发 /api，这两个接口只有容器内部访问得到；
# 只涉及数量，不含案件信息
@app.post("/drain")
async def drain() -> dict:
    """进入排空：此后新的分析 / 断点恢复返回 503，进行中的照常跑完。重启后的新进程恢复正常。"""
    start_draining()
    return {"draining": True, "running": active_runs()}


@app.get("/busy")
async def busy() -> dict:
    """正在执行的分析 / 断点恢复数（含分析结束后把请求编号落盘），部署脚本等它归零再重启。"""
    return {"running": active_runs(), "draining": is_draining()}


# 健康检查分两层（#76），都不在 /api 下、nginx 不转发，只有容器内部访问得到：
# /healthz 只说明进程活着（docker HEALTHCHECK 用，挂了就重启）；/readyz 说明能正常处理案件
# （checkpoint 库能读写、磁盘够、Key 已配置），部署脚本用它判断新版本能否上线。
@app.get("/healthz")
@app.get("/")  # 旧地址，保留给还在用它的检查
async def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "服务运行正常"}


@app.get("/readyz")
async def ready_check() -> JSONResponse:
    try:
        await checkpoint.probe()
        db = readiness.Check("checkpoint 库读写", True, "正常")
    except Exception as exc:  # noqa: BLE001 — 任何失败都算未就绪，只报异常类型
        db = readiness.Check("checkpoint 库读写", False, f"失败（{type(exc).__name__}）")
    min_mb = config.READY_MIN_FREE_MB
    checks = [
        db,
        readiness.check_disk("上传目录磁盘空间", file_store.UPLOAD_DIR, min_mb),
        readiness.check_disk("checkpoint 目录磁盘空间", config.CHECKPOINT_DB_PATH, min_mb),
        readiness.check_disk("运行日志目录磁盘空间", run_log.RUN_LOG_PATH, min_mb),
        readiness.check_key("DEEPSEEK_API_KEY", config.DEEPSEEK_API_KEY),
        readiness.check_key("DASHSCOPE_API_KEY", config.DASHSCOPE_API_KEY),
    ]
    body = readiness.summarize(checks)
    return JSONResponse(body, status_code=200 if body["ready"] else 503)
