# FastAPI 应用入口：注册路由、配置 CORS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.routers import files, extract, generate

app = FastAPI(title="安装合同纠纷起诉状生成系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files.router, prefix="/api")
app.include_router(extract.router, prefix="/api")
app.include_router(generate.router, prefix="/api")



@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "服务运行正常"}
