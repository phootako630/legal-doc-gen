# Prompt 加载器：从 prompts/ 目录读取 markdown 模板，注入 {{变量}} 后返回完整 Prompt 字符串
from __future__ import annotations

import hashlib
from pathlib import Path

from app.services import run_log

# prompts/ 目录相对于项目根（backend 的上一层）
_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"


def load_prompt(filename: str, variables: dict[str, str] | None = None) -> str:
    """
    读取 prompts/{filename}，将所有 {{key}} 替换为 variables[key]。
    未提供的变量保持原样，方便调试时发现遗漏。
    """
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在：{path}")

    template = path.read_text(encoding="utf-8")
    # 运行日志的 llm_call 带上「用的是哪版提示词」：文件名 + 模板（填变量前）内容哈希，定位提示词改动
    run_log.set_prompt(f"{filename}@{hashlib.sha256(template.encode()).hexdigest()[:8]}")

    if variables:
        for key, value in variables.items():
            template = template.replace(f"{{{{{key}}}}}", value)

    return template
