# POST /api/analyze：启动 agent（清点→抽取→代码校验），返回 CaseState（可能带断点）
# GET  /api/analyze/progress：agent 三阶段的实时进度，供前端轮询渲染（与 extract 共用 llm_progress）
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runner import run_analyze
from app.agent.state import CaseState
from app.services import llm_progress

router = APIRouter()


class PageText(BaseModel):
    page: int
    text: str


class FileInput(BaseModel):
    filename: str
    text: str
    is_scanned: bool = False
    identified_type: str = "未知"
    # 逐页文本，供抽取后定位真实页码；旧客户端可不带
    pages: list[PageText] = []


class AnalyzeRequest(BaseModel):
    files: list[FileInput]
    internet_allowed: bool = True


class LlmProgress(BaseModel):
    active: bool
    stage: str  # '材料清点' | '字段抽取' | '校验高亮' | ''
    stage_index: int
    total_stages: int
    stage_elapsed_s: int


@router.post("/analyze", response_model=CaseState)
async def analyze(req: AnalyzeRequest) -> CaseState:
    """启动 LangGraph agent；命中 interrupt 时返回带 pending 的 CaseState 供律师决策。"""
    # 三阶段（清点/抽取/校验高亮）与 extract 一致；包一层进度记录，异常时也能复位
    llm_progress.begin(total_stages=3)
    try:
        return await run_analyze(
            [f.model_dump() for f in req.files], req.internet_allowed
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e
    finally:
        llm_progress.finish()


@router.get("/analyze/progress", response_model=LlmProgress)
async def get_analyze_progress() -> LlmProgress:
    """返回当前 agent 任务的处理阶段（v1 单用户全局态，与 extract 共享同一进度模块）。"""
    return LlmProgress(**llm_progress.snapshot())
