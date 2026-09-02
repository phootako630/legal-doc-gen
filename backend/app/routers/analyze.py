# POST /api/analyze：启动 agent（清点→抽取→代码校验），返回 CaseState（可能带断点）
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runner import run_analyze
from app.agent.state import CaseState

router = APIRouter()


class FileInput(BaseModel):
    filename: str
    text: str
    is_scanned: bool = False
    identified_type: str = "未知"


class AnalyzeRequest(BaseModel):
    files: list[FileInput]
    internet_allowed: bool = True


@router.post("/analyze", response_model=CaseState)
async def analyze(req: AnalyzeRequest) -> CaseState:
    """启动 LangGraph agent；命中 interrupt 时返回带 pending 的 CaseState 供律师决策。"""
    try:
        return await run_analyze([f.model_dump() for f in req.files], req.internet_allowed)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e
