# POST /api/extract：接收文件文本，依次调用 LLM 完成清点、抽取、校验三步
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.llm_client import chat
from app.services.prompt_loader import load_prompt

router = APIRouter()


class FileInput(BaseModel):
    filename: str
    text: str
    is_scanned: bool = False
    identified_type: str = "未知"


class ExtractRequest(BaseModel):
    files: list[FileInput]
    internet_allowed: bool = True


class ExtractResponse(BaseModel):
    extracted_fields: dict  # type: ignore[type-arg]
    validation_report: str
    highlight_list: str


def _build_combined_text(files: list[FileInput]) -> str:
    """拼装带文件名/类型/OCR标记的文本块，给模型结构化的出处依据。"""
    blocks: list[str] = []
    for f in files:
        tag = "（本文件为 OCR 扫描识别，文字可能存在误差）" if f.is_scanned else ""
        blocks.append(f"【文件：{f.filename}｜{f.identified_type}】{tag}\n{f.text}")
    return "\n\n---\n\n".join(blocks)


def _mark_ocr_fields(data: Any, scanned_filenames: set[str]) -> None:
    """
    递归遍历 extracted_fields，若某字段的 src 引用了扫描件文件名，
    代码层面确保 src 中带有 OCR 标记——不依赖模型是否记得主动声明，
    因为前端 inferStatus() 是靠 src 文本里的"OCR"/"扫描"关键词判定待核实状态的。
    """
    if not scanned_filenames:
        return
    if isinstance(data, dict):
        if "value" in data and "src" in data and isinstance(data.get("src"), str):
            src = data["src"]
            if src and "OCR" not in src and any(fn in src for fn in scanned_filenames):
                data["src"] = f"{src}（OCR识别，请核实）"
            return
        for v in data.values():
            _mark_ocr_fields(v, scanned_filenames)
    elif isinstance(data, list):
        for item in data:
            _mark_ocr_fields(item, scanned_filenames)


@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest) -> ExtractResponse:
    """三步 LLM 调用：材料清点 → 字段抽取 → 校验高亮。"""
    combined_text = _build_combined_text(req.files)
    scanned_filenames = {f.filename for f in req.files if f.is_scanned}

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

    # 代码层面补全 OCR 标记（见 _mark_ocr_fields 说明），不依赖模型是否记得声明
    _mark_ocr_fields(extracted_fields, scanned_filenames)

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
