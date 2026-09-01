# confidence.py 单测：软信号加权评分 + 硬信号状态映射优先级
from app.services.confidence import (
    CONFIDENCE_UNCERTAIN_BELOW,
    ConfidenceSignals,
    compute_confidence,
    map_status,
)


# ── 评分 ──────────────────────────────────────────────────────────────────
def test_full_signals_max_score():
    # 文本来源 + 精确锚定 + 自一致 + 模型满分 = 100
    s = ConfidenceSignals("text", "exact", True, 1.0)
    assert compute_confidence(s) == 100


def test_text_exact_without_consistency_or_model():
    # 40*1.0 + 30*1.0 + 20*0 + 10*0 = 70
    s = ConfidenceSignals("text", "exact", None, None)
    assert compute_confidence(s) == 70


def test_ocr_fuzzy_low_score():
    # 40*0.6 + 30*0.5 + 0 + 0 = 24 + 15 = 39
    s = ConfidenceSignals("ocr", "fuzzy", None, None)
    assert compute_confidence(s) == 39


def test_none_anchor_and_channel_default():
    # 未知通道回退为 text；无锚定 → 40*1.0 = 40
    s = ConfidenceSignals("text", "none", None, None)
    assert compute_confidence(s) == 40


def test_model_confidence_clamped():
    s = ConfidenceSignals("text", "none", None, 5.0)  # 越界 → 裁剪到 1.0
    assert compute_confidence(s) == 50  # 40 + 10*1.0


def test_score_bounds():
    assert 0 <= compute_confidence(ConfidenceSignals("ocr", "none", None, None)) <= 100


# ── 状态映射优先级 ────────────────────────────────────────────────────────
def test_conflict_has_highest_priority():
    # 即便值存在、锚定命中、置信度高，只要有冲突就是 conflict
    st = map_status(
        value="6", anchor_hit=True, channel="text", has_conflict=True, confidence=95
    )
    assert st == "conflict"


def test_missing_when_value_none():
    st = map_status(
        value=None, anchor_hit=False, channel="text", has_conflict=False, confidence=0
    )
    assert st == "missing"


def test_missing_when_anchor_failed_even_if_value_present():
    # 锚定失败 = 反幻觉，判缺失
    st = map_status(
        value="某公司", anchor_hit=False, channel="text", has_conflict=False, confidence=80
    )
    assert st == "missing"


def test_ocr_channel_is_uncertain():
    st = map_status(
        value="6", anchor_hit=True, channel="ocr", has_conflict=False, confidence=90
    )
    assert st == "ocr_uncertain"


def test_low_confidence_is_uncertain():
    st = map_status(
        value="6",
        anchor_hit=True,
        channel="text",
        has_conflict=False,
        confidence=CONFIDENCE_UNCERTAIN_BELOW - 1,
    )
    assert st == "ocr_uncertain"


def test_normal_when_all_good():
    st = map_status(
        value="6", anchor_hit=True, channel="text", has_conflict=False, confidence=85
    )
    assert st == "normal"


def test_empty_string_is_missing():
    st = map_status(
        value="   ", anchor_hit=True, channel="text", has_conflict=False, confidence=90
    )
    assert st == "missing"
