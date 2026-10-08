# POST /api/feedback：律师在审核页对某个字段（或整体）提交问题反馈
#
# 保密分层：
#   - 文字说明可能含案件内容 → 存进案件数据库（checkpoint 同库、同权限、随会话过期清除）；
#   - 运行日志只记 run_id、字段 key、问题分类、有无文字说明，绝不记说明内容。
# 开发者在服务器上用 scripts/list_feedback.py 查看（见 deploy/README.md）。
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent import checkpoint
from app.services import run_log

router = APIRouter()

# 问题分类（固定枚举，可安全写入运行日志）
FeedbackCategory = Literal["wrong_value", "missing", "wrong_source", "other"]


class FeedbackRequest(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    # 针对的字段 key（如 defendant_name）；为空表示整体反馈
    field_key: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    category: FeedbackCategory
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    ok: bool


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """保存律师反馈；会话不存在或已过期时拒绝（反馈必须能对应到具体案件）。"""
    if not await checkpoint.run_exists(req.run_id) or await checkpoint.is_expired(
        req.run_id
    ):
        raise HTTPException(status_code=404, detail="会话不存在或已过期，反馈未保存")
    comment = (req.comment or "").strip() or None
    await checkpoint.save_feedback(req.run_id, req.field_key, req.category, comment)
    run_log.log_event(
        "feedback",
        run_id=req.run_id,
        field_key=req.field_key,
        category=req.category,
        has_comment=comment is not None,
    )
    return FeedbackResponse(ok=True)
