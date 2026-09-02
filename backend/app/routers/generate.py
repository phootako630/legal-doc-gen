# POST /api/generate：接收律师确认后的字段 JSON，确定性渲染起诉状全文
#
# v2 第 4 步：不再调用 LLM 自由生成，改为 complaint_renderer 模板填槽（非 LLM）。
# 标注（缺失/冲突/OCR）由代码按字段状态贴，稳定可控；prompt-b-generate.md 降为 v1 遗留。
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.complaint_renderer import render_complaint

router = APIRouter()


class GenerateRequest(BaseModel):
    validated_json: dict  # type: ignore[type-arg]


class GenerateResponse(BaseModel):
    complaint_text: str


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """将律师确认的字段模板填槽为起诉状全文（确定性渲染，非 LLM）。"""
    try:
        complaint_text = render_complaint(req.validated_json)
    except FileNotFoundError as e:
        # 模板文件缺失属部署问题，给出明确中文错误
        raise HTTPException(status_code=500, detail=f"起诉状模板缺失：{e}") from e

    if not complaint_text.strip():
        raise HTTPException(status_code=500, detail="起诉状生成失败：渲染结果为空")

    return GenerateResponse(complaint_text=complaint_text)
