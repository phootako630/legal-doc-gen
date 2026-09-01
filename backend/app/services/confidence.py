# ③ 可信度分层评分：硬信号定状态（是/否），软信号给 0-100 confidence（仅排序用）
#
# 设计意图（对应 CLAUDE.md「可信度分层」）：
#   律师不信任黑盒总分。confidence 只服务 UI 优先级（排序、默认展开），不替代判断。
#   状态（FieldStatus）由确定性硬信号决定，与 confidence 分离：
#     勾稽冲突/台数不一致        → conflict（最高优先级）
#     value=null 或锚定失败       → missing
#     来源含 OCR/扫描 或 conf<60  → ocr_uncertain
#     其余                        → normal
#   本模块纯确定性、可单测，不调用任何 LLM。
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.anchoring import AnchorQuality

SourceChannel = Literal["text", "ocr", "multimodal"]
FieldStatus = Literal["normal", "missing", "conflict", "ocr_uncertain"]

# 低于此 confidence 标记为"待核实"（对齐 CLAUDE.md config 的 CONFIDENCE_UNCERTAIN_BELOW）
CONFIDENCE_UNCERTAIN_BELOW = 60

# ── 软信号权重（Σ=100）与取值表 ─────────────────────────────────────────────
_WEIGHT_CHANNEL = 40
_WEIGHT_ANCHOR = 30
_WEIGHT_CONSISTENCY = 20
_WEIGHT_MODEL = 10

# 来源通道可靠性：文本最高，OCR 最低，多模态居中
_CHANNEL_SCORE: dict[str, float] = {"text": 1.0, "ocr": 0.6, "multimodal": 0.7}
# 锚定质量：精确命中满分，模糊命中半分，未命中 0
_ANCHOR_SCORE: dict[str, float] = {"exact": 1.0, "fuzzy": 0.5, "none": 0.0}


@dataclass(frozen=True)
class ConfidenceSignals:
    """计算 confidence 所需的软信号。缺失的信号按最保守（0）计。"""

    channel: SourceChannel = "text"
    anchor_quality: AnchorQuality = "none"
    # 两次抽取/两模型是否一致；None 表示未做自一致性检查（按 0 计）
    self_consistent: bool | None = None
    # 模型自评置信度 0-1；None 表示无（按 0 计）
    model_confidence: float | None = None


def compute_confidence(signals: ConfidenceSignals) -> int:
    """按加权公式算 0-100 的软置信度（四舍五入取整、裁剪到 [0,100]）。"""
    channel_score = _CHANNEL_SCORE.get(signals.channel, _CHANNEL_SCORE["text"])
    anchor_score = _ANCHOR_SCORE.get(signals.anchor_quality, 0.0)
    consistency_score = 1.0 if signals.self_consistent else 0.0
    model_score = 0.0
    if signals.model_confidence is not None:
        model_score = max(0.0, min(1.0, signals.model_confidence))

    raw = (
        _WEIGHT_CHANNEL * channel_score
        + _WEIGHT_ANCHOR * anchor_score
        + _WEIGHT_CONSISTENCY * consistency_score
        + _WEIGHT_MODEL * model_score
    )
    return max(0, min(100, round(raw)))


def map_status(
    *,
    value: object,
    anchor_hit: bool,
    channel: SourceChannel,
    has_conflict: bool,
    confidence: int,
    uncertain_below: int = CONFIDENCE_UNCERTAIN_BELOW,
) -> FieldStatus:
    """
    按硬信号把字段映射到 FieldStatus，优先级从高到低：
      conflict > missing > ocr_uncertain > normal
    - has_conflict：该字段参与了某项失败的确定性校验（金额/台数等）
    - value 为空 或 锚定失败：missing（反幻觉置空后也走此支）
    - 来源为 OCR 或 confidence 偏低：ocr_uncertain（待核实）
    """
    if has_conflict:
        return "conflict"
    if value is None or (isinstance(value, str) and value.strip() == "") or not anchor_hit:
        return "missing"
    if channel == "ocr" or confidence < uncertain_below:
        return "ocr_uncertain"
    return "normal"
