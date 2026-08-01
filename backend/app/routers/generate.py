# POST /api/generate：接收律师确认后的字段 JSON，生成起诉状全文
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.llm_client import chat
from app.services.prompt_loader import load_prompt

router = APIRouter()


class GenerateRequest(BaseModel):
    validated_json: dict  # type: ignore[type-arg]


class GenerateResponse(BaseModel):
    complaint_text: str


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """将律师确认的字段 JSON 和起诉状模板注入 Prompt B，调用 LLM 生成起诉状全文。"""
    complaint_template = load_prompt("complaint-template.md")
    prompt = load_prompt(
        "prompt-b-generate.md",
        {
            "complaint_template": complaint_template,
            "validated_json": json.dumps(req.validated_json, ensure_ascii=False, indent=2),
        },
    )
    try:
        complaint_text: str = await chat([{"role": "user", "content": prompt}])
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"起诉状生成失败：{e}") from e

    if not complaint_text.strip():
        raise HTTPException(status_code=500, detail="起诉状生成失败：LLM 返回为空")

    return GenerateResponse(complaint_text=complaint_text)
