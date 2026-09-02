# POST /api/resume：律师在断点处给出决定后恢复 agent，返回更新后的 CaseState
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runner import run_resume
from app.agent.state import CaseState

router = APIRouter()


class ResumeRequest(BaseModel):
    run_id: str
    # 冲突选值 / 补录：{字段key: 取值}
    decisions: dict[str, Any] = {}


@router.post("/resume", response_model=CaseState)
async def resume(req: ResumeRequest) -> CaseState:
    """在 interrupt 断点处提交决定并恢复；可能再次返回 pending，直至 pending=null。"""
    try:
        return await run_resume(req.run_id, req.decisions)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="会话不存在或已过期，请重新分析") from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"恢复失败：{e}") from e
