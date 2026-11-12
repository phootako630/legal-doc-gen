# POST /api/generate：接收律师确认后的字段 JSON，确定性渲染起诉状全文
#
# v2 第 4 步：不再调用 LLM 自由生成，改为 complaint_renderer 模板填槽（非 LLM）。
# 标注（缺失/冲突/OCR）由代码按字段状态贴，稳定可控；prompt-b-generate.md 降为 v1 遗留。
# 填空值包在 ⟦…⟧ 里返回，前端预览与 Word 导出据此标黄（律师确认单第 19 题）。
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runner import delivered_state
from app.services import case_snapshot, run_log
from app.services.complaint_renderer import render_complaint

router = APIRouter()


class GenerateRequest(BaseModel):
    validated_json: dict  # type: ignore[type-arg]
    # 可选：关联 agent 会话，用于统计律师相对 agent 的改动（缺省则不统计）
    run_id: str | None = None


class GenerateResponse(BaseModel):
    complaint_text: str


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """将律师确认的字段模板填槽为起诉状全文（确定性渲染，非 LLM）。"""
    if req.run_id:
        try:
            state = await delivered_state(req.run_id, run_log.current_user())
        except Exception:  # noqa: BLE001 — 统计改动是尽力而为，不能挡住生成起诉状
            state = None
        delivered = state["fields"] if state else None
        run_log.log_lawyer_edits(req.run_id, delivered, req.validated_json)
        # 线上案件快照（#157）：律师最终确认的字段就是这个案件的标准答案；
        # 交付时的校验结论与就绪度一并记下，统计时按审核页口径还原字段状态（#74）
        case_snapshot.finish(req.run_id, delivered, req.validated_json, run_log.current_user(),
                             review={k: state[k] for k in ("validations", "readiness")} if state else None)
    try:
        complaint_text = render_complaint(req.validated_json, mark_fills=True)
    except FileNotFoundError as e:
        # 模板文件缺失属部署问题，给出明确中文错误
        raise HTTPException(status_code=500, detail=f"起诉状模板缺失：{e}") from e

    if not complaint_text.strip():
        raise HTTPException(status_code=500, detail="起诉状生成失败：渲染结果为空")

    return GenerateResponse(complaint_text=complaint_text)
