# POST /api/analyze：启动 agent（清点→抽取→代码校验），返回 CaseState（可能带断点）
# GET  /api/analyze/progress?progress_id=<请求编号>：该次分析三阶段的实时进度，供前端轮询
#
# 幂等：请求头 Idempotency-Key（前端每次上传生成一个）相同的重复请求只分析一次，
# 详见 agent/runner.py 的 run_analyze_once。不带该请求头时行为与以前一致（每次新分析）。
import re

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.agent.runner import (
    Draining,
    IdempotencyConflict,
    analyze_result,
    run_analyze,
    run_analyze_once,
)
from app.agent.state import CaseState
from app.services import llm_progress, run_log
from app.services.file_parser import DocType

router = APIRouter()


class PageText(BaseModel):
    page: int
    text: str


class FileInput(BaseModel):
    filename: str
    text: str
    is_scanned: bool = False
    identified_type: DocType = "未知"
    # 系统原先识别的类型：与 identified_type 不同说明律师在上传页改过，记入运行日志改进识别规则
    detected_type: DocType | None = None
    # 总页数：按需 OCR 逐页遍历的上限。缺了它扫描合同会被当成 0 页、一页都不 OCR
    page_count: int = 0
    # 逐页文本，供抽取后定位真实页码；旧客户端可不带
    pages: list[PageText] = []
    # 扫描件暂存字节引用，供 agent 按需逐页 OCR
    file_id: str | None = None


class AnalyzeRequest(BaseModel):
    files: list[FileInput]
    internet_allowed: bool = True


class LlmProgress(BaseModel):
    active: bool
    stage: str  # '材料清点' | '字段抽取' | '校验高亮' | ''
    stage_index: int
    total_stages: int
    stage_elapsed_s: int


# 部署排空期间（重启前最多几分钟）拒绝新任务时给律师的提示
DRAINING_DETAIL = "系统正在更新，请一两分钟后再试"

# 请求编号只允许字母、数字、- 和 _（UUID 即可），长度 8–128，避免把任意内容写进库里
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _log_type_overrides(files: list[FileInput]) -> None:
    """律师在上传页改过的文件类型：只记「从什么类型改成什么类型」，不记文件名（可能含当事人名称）。"""
    changes = [
        {"from": f.detected_type, "to": f.identified_type}
        for f in files
        if f.detected_type is not None and f.detected_type != f.identified_type
    ]
    if changes:
        run_log.log_event("file_type_override", changes=changes)


@router.post("/analyze", response_model=CaseState)
async def analyze(
    req: AnalyzeRequest,
    idempotency_key: str | None = Header(default=None),
) -> CaseState:
    """启动 LangGraph agent；命中 interrupt 时返回带 pending 的 CaseState 供律师决策。"""
    files = [f.model_dump() for f in req.files]
    _log_type_overrides(req.files)
    if idempotency_key is not None:
        if not _KEY_RE.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="请求编号格式不正确")
        try:
            # 进度记录在 run_analyze_once 的任务里做：重复请求不会把进度条重置
            return await run_analyze_once(
                idempotency_key, files, req.internet_allowed, run_log.current_user()
            )
        except Draining as e:
            raise HTTPException(status_code=503, detail=DRAINING_DETAIL) from e
        except IdempotencyConflict as e:
            raise HTTPException(
                status_code=409,
                detail="这个请求编号已用于另一组材料，请重新上传后再分析",
            ) from e
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e

    # 不带请求编号的旧客户端：照常分析，但没有 id 可挂进度，故不记录进度
    try:
        return await run_analyze(files, req.internet_allowed, run_log.current_user())
    except Draining as e:
        raise HTTPException(status_code=503, detail=DRAINING_DETAIL) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e


@router.get("/analyze/result", response_model=CaseState)
async def get_analyze_result(key: str = Query(...)) -> CaseState:
    """
    处理页刷新后接回同一次分析：还在跑就等结果，已跑完直接返回；找不到（失败 / 服务重启 / 过期）404。
    """
    if not _KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="请求编号格式不正确")
    try:
        case = await analyze_result(key, run_log.current_user())
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e
    if case is None:
        raise HTTPException(
            status_code=404,
            detail="上次的分析没有完成（可能失败或服务已重启），请重新上传材料",
        )
    return case


@router.get("/analyze/progress", response_model=LlmProgress)
async def get_analyze_progress(
    # 即分析请求的 Idempotency-Key：多人同时分析时各看各的进度
    progress_id: str | None = Query(default=None, max_length=128),
) -> LlmProgress:
    """返回指定分析任务的处理阶段；未知或已结束的任务返回非活跃状态。"""
    return LlmProgress(**llm_progress.snapshot(progress_id))
