# POST /api/extract：接收文件文本，依次调用 LLM 完成清点、抽取、校验三步
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.llm_client import chat
from app.services.prompt_loader import load_prompt

router = APIRouter()


class ExtractRequest(BaseModel):
    files_text: list[str]
    internet_allowed: bool = True


class ExtractResponse(BaseModel):
    extracted_fields: dict  # type: ignore[type-arg]
    validation_report: str
    highlight_list: str


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest) -> ExtractResponse:
    """三步 LLM 调用：材料清点 → 字段抽取 → 校验高亮。"""
    combined_text = "\n\n---\n\n".join(req.files_text)

    # ── Step 1：材料清点（json_mode=True）──────────────────────────────────
    checklist_prompt = load_prompt("prompt-a-checklist.md", {"files_text": combined_text})
    try:
        checklist: dict = await chat(  # type: ignore[type-arg]
            [{"role": "user", "content": checklist_prompt}], json_mode=True
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"材料清点失败：{e}") from e

    # can_proceed=false 表示 LLM 判断材料不足，提前终止
    if not checklist.get("can_proceed", True):
        missing: list[str] = checklist.get("missing", [])
        notes: str = checklist.get("notes", "")
        detail = f"材料不足，无法继续处理。缺少：{'、'.join(missing) if missing else '未知'}"
        if notes:
            detail += f"。备注：{notes}"
        raise HTTPException(status_code=422, detail=detail)

    # 序列化清单，注入后续两个 Prompt
    checklist_summary = json.dumps(checklist, ensure_ascii=False, indent=2)

    # ── Step 2：字段抽取（json_mode=True）──────────────────────────────────
    extract_prompt = load_prompt(
        "prompt-a-extract.md",
        {
            "files_text": combined_text,
            "material_checklist": checklist_summary,
            "internet_allowed": str(req.internet_allowed),
        },
    )
    try:
        extracted_fields: dict = await chat(  # type: ignore[type-arg]
            [{"role": "user", "content": extract_prompt}], json_mode=True
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"字段抽取失败：{e}") from e

    # ── Step 3：校验高亮（json_mode=False，返回文本）──────────────────────
    validate_prompt = load_prompt(
        "prompt-a-validate.md",
        {
            "extracted_json": json.dumps(extracted_fields, ensure_ascii=False),
            "material_checklist": checklist_summary,
        },
    )
    try:
        validate_raw: str = await chat([{"role": "user", "content": validate_prompt}])
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"校验失败：{e}") from e

    # 约定：校验报告和高亮列表以 "---HIGHLIGHT---" 分隔
    parts = validate_raw.split("---HIGHLIGHT---", 1)
    validation_report = parts[0].strip()
    highlight_list = parts[1].strip() if len(parts) > 1 else ""

    return ExtractResponse(
        extracted_fields=extracted_fields,
        validation_report=validation_report,
        highlight_list=highlight_list,
    )
