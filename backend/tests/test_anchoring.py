# anchoring.py 单测：覆盖精确/金额等价/日期等价/去空白 fuzzy/未命中/反幻觉
from app.services.anchoring import anchor_value


def test_exact_substring_hit():
    r = anchor_value("北京华龙电梯有限公司", "被告：北京华龙电梯有限公司，住所地……")
    assert r.hit is True
    assert r.quality == "exact"
    assert r.position is not None


def test_amount_equivalence_with_thousands_and_currency():
    # 抽取值 894934，原文写作带千分位与货币符号 → 数值等价，精确命中
    r = anchor_value(894934, "合同总价为 ￥894,934.00 元")
    assert r.hit is True
    assert r.quality == "exact"


def test_amount_decimal_equivalence():
    r = anchor_value("638667.2", "第一期款 638,667.20")
    assert r.hit is True
    assert r.quality == "exact"


def test_date_equivalence_across_formats():
    # 抽取值中文写法，原文 ISO 写法 → 日期等价命中
    r = anchor_value("2023年5月1日", "验收合格日期：2023-05-01")
    assert r.hit is True
    assert r.quality == "exact"


def test_date_equivalence_slash_format():
    r = anchor_value("2023-05-01", "签订日期 2023/5/1")
    assert r.hit is True
    assert r.quality == "exact"


def test_fuzzy_hit_after_stripping_whitespace():
    # 原文里值被空白/换行打断 → 精确失败，去空白后命中为 fuzzy
    r = anchor_value("华龙电梯", "华龙\n 电梯")
    assert r.hit is True
    assert r.quality == "fuzzy"


def test_no_hit_is_hallucination_signal():
    r = anchor_value("上海某某科技公司", "被告：北京华龙电梯有限公司")
    assert r.hit is False
    assert r.quality == "none"
    assert r.position is None


def test_wrong_amount_does_not_match():
    r = anchor_value(999999, "合同总价 894934 元")
    assert r.hit is False


def test_none_and_empty_inputs():
    assert anchor_value(None, "任意文本").hit is False
    assert anchor_value("值", "").hit is False
    assert anchor_value("   ", "有内容").hit is False


def test_fullwidth_normalization():
    # 全角数字/字母经 NFKC 归一后应命中
    r = anchor_value("ABC123", "编号：ＡＢＣ１２３")
    assert r.hit is True
    assert r.quality == "exact"
