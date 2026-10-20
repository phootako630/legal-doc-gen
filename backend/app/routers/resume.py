# POST /api/resume：律师在断点处给出决定后恢复 agent，返回更新后的 CaseState
# GET  /api/case/{run_id}：取回会话当前状态（页面刷新后「继续上次的案件」），过期 / 不存在 404
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.runner import get_case, run_resume
from app.agent.state import CaseState
from app.services import llm_progress

router = APIRouter()

# run_id 是 uuid4().hex；其他格式直接按不存在处理，不拿去查库
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ResumeRequest(BaseModel):
    run_id: str
    # 冲突选值 / 补录：{字段key: 取值}
    decisions: dict[str, Any] = {}
    # 可选进度 id：前端需要展示恢复进度时带上（与分析进度同一套接口查询）
    progress_id: str | None = Field(default=None, max_length=128)


@router.post("/resume", response_model=CaseState)
async def resume(req: ResumeRequest) -> CaseState:
    """在 interrupt 断点处提交决定并恢复；可能再次返回 pending，直至 pending=null。"""
    # 恢复后仅重跑校验节点（校验高亮阶段），仍用同一进度模块驱动前端等待提示
    with llm_progress.track(req.progress_id, total_stages=3):
        try:
            return await run_resume(req.run_id, req.decisions)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail="会话不存在或已过期，请重新分析"
            ) from e
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=f"恢复失败：{e}") from e


@router.get("/case/{run_id}", response_model=CaseState)
async def get_case_state(run_id: str) -> CaseState:
    """会话当前状态（与 resume 同样只凭 run_id 访问；过期会话按不存在处理）。"""
    case = await get_case(run_id) if _RUN_ID_RE.fullmatch(run_id) else None
    if case is None:
        raise HTTPException(status_code=404, detail="上次的案件已过期或不存在，请重新上传材料")
    return case
