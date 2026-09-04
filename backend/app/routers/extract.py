# POST /api/extract：接收文件文本，依次调用 LLM 完成清点、抽取、校验三步
# GET /api/extract/progress：三步 LLM 处理的实时阶段，供前端轮询渲染进度
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import llm_progress
from app.services.extraction import (
    build_combined_text,
    enrich_provenance,
    format_checks_for_llm,
    mark_ocr_fields,
)
from app.services.llm_client import chat
from app.services.prompt_loader import load_prompt
from app.services.validators import check_to_dict, run_all_checks

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
    # 扫描件暂存字节引用，供 agent 按需逐页 OCR
    file_id: str | None = None


class ExtractRequest(BaseModel):
    files: list[FileInput]
    internet_allowed: bool = True


class ExtractResponse(BaseModel):
    extracted_fields: dict  # type: ignore[type-arg]
    # 确定性代码校验结论（金额勾稽/台数三源/信用代码/日期），是冲突判断的权威来源。
    # LLM 不再做一致性判断，validation_report/highlight_list 仅为据此生成的说明文本。
    validations: list[dict]  # type: ignore[type-arg]
    validation_report: str
    highlight_list: str


class LlmProgress(BaseModel):
    active: bool
    stage: str  # '材料清点' | '字段抽取' | '校验高亮' | ''
    stage_index: int
    total_stages: int
    stage_elapsed_s: int


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest) -> ExtractResponse:
    """三步 LLM 调用入口：包一层进度记录，保证异常时进度态也能复位。"""
    llm_progress.begin(total_stages=3)
    try:
        return await _extract_impl(req)
    finally:
        llm_progress.finish()


@router.get("/extract/progress", response_model=LlmProgress)
async def get_extract_progress() -> LlmProgress:
    """返回当前抽取任务的处理阶段（v1 单用户全局态，无任务 ID）。"""
    return LlmProgress(**llm_progress.snapshot())


async def _extract_impl(req: ExtractRequest) -> ExtractResponse:
    """三步 LLM 调用：材料清点 → 字段抽取 → 校验高亮。"""
    files = [f.model_dump() for f in req.files]
    combined_text = build_combined_text(files)
    scanned_filenames = {f.filename for f in req.files if f.is_scanned}

    # ── Step 1：材料清点（json_mode=True）──────────────────────────────────
    llm_progress.start_stage("材料清点", 1)
    checklist_prompt = load_prompt(
        "prompt-a-checklist.md", {"files_text": combined_text}
    )
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
        detail = (
            f"材料不足，无法继续处理。缺少：{'、'.join(missing) if missing else '未知'}"
        )
        if notes:
            detail += f"。备注：{notes}"
        raise HTTPException(status_code=422, detail=detail)

    # 序列化清单，注入后续两个 Prompt
    checklist_summary = json.dumps(checklist, ensure_ascii=False, indent=2)

    # ── Step 2：字段抽取（json_mode=True）──────────────────────────────────
    llm_progress.start_stage("字段抽取", 2)
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

    # 代码层面补全 OCR 标记（见 mark_ocr_fields 说明），不依赖模型是否记得声明
    mark_ocr_fields(extracted_fields, scanned_filenames)
    # 值回原文逐页锚定：补充已验证页码 + 命中片段 + 通道 + confidence（出处可追溯）
    enrich_provenance(extracted_fields, files)

    # ── Step 3a：确定性交叉校验（代码，非 LLM）────────────────────────────
    # 金额勾稽/台数三源/信用代码/日期一律由 validators.py 判定，是冲突结论的权威来源。
    checks = run_all_checks(extracted_fields)
    validations = [check_to_dict(c) for c in checks]

    # ── Step 3b：LLM 仅据确定性结论生成给律师看的说明文本（不做一致性判断）──
    llm_progress.start_stage("校验高亮", 3)
    validate_prompt = load_prompt(
        "prompt-a-validate.md",
        {
            "extracted_json": json.dumps(extracted_fields, ensure_ascii=False),
            "deterministic_checks": format_checks_for_llm(checks),
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
        validations=validations,
        validation_report=validation_report,
        highlight_list=highlight_list,
    )
