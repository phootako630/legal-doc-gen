# complaint_renderer 单测：人民币大写 + 模板填槽 + 代码按状态贴标注
from app.services.complaint_renderer import (
    amount_to_chinese_rmb,
    render_complaint,
)

TEMPLATE = (
    "款{{unpaid_amount}}；签订{{contract_sign_date}}；"
    "台数{{elevator_qty}}台；被告{{defendant_name}}；"
    "法院{{court_name}}人民法院"
)


# ── 人民币大写 ────────────────────────────────────────────────────────────
def test_rmb_integer_zheng():
    assert amount_to_chinese_rmb(894934) == "捌拾玖万肆仟玖佰叁拾肆元整"


def test_rmb_with_jiao():
    assert amount_to_chinese_rmb(638667.2) == "陆拾叁万捌仟陆佰陆拾柒元贰角"
    assert amount_to_chinese_rmb(256266.8) == "贰拾伍万陆仟贰佰陆拾陆元捌角"


def test_rmb_reconcile_sample_sum():
    # CLAUDE.md 真实样例：638667.2 + 256266.8 = 894934
    assert amount_to_chinese_rmb(894934) == "捌拾玖万肆仟玖佰叁拾肆元整"


def test_rmb_zeros_and_sections():
    assert amount_to_chinese_rmb(0) == "零元整"
    assert amount_to_chinese_rmb(100) == "壹佰元整"
    assert amount_to_chinese_rmb(100000) == "壹拾万元整"
    assert amount_to_chinese_rmb(1000000) == "壹佰万元整"
    assert amount_to_chinese_rmb(10005) == "壹万零伍元整"
    assert amount_to_chinese_rmb(100000001) == "壹亿零壹元整"


def test_rmb_jiao_fen():
    assert amount_to_chinese_rmb(123.45) == "壹佰贰拾叁元肆角伍分"
    # 元与分之间需补零（角为 0）
    assert amount_to_chinese_rmb(100.05) == "壹佰元零伍分"


def test_rmb_parse_failure_returns_none():
    assert amount_to_chinese_rmb("无") is None
    assert amount_to_chinese_rmb(None) is None


# ── 渲染 + 标注 ───────────────────────────────────────────────────────────
def _fields(**overrides):
    base = {
        "total_amount": {"value": 894934, "src": "《审批表》"},
        "paid_amount": {"value": 638667.2, "src": "《审批表》"},
        "unpaid_amount": {"value": 256266.8, "src": "《审批表》"},
        "elevator_qty": {"value": 6, "src": "《审批表》"},
        "elevator_qty_by_approval": {"value": 6, "src": "《审批表》"},
        "elevator_qty_by_contract": {"value": 6, "src": "《合同》"},
        "elevator_qty_by_acceptance": {"value": 6, "src": "《验收报告》"},
        "contract_sign_date": {"value": "2023年5月1日", "src": "《审批表》"},
        "defendant_name": {"value": "北京华龙电梯有限公司", "src": "《审批表》"},
    }
    base.update(overrides)
    return base


def test_render_normal_fills_slots_with_amounts_and_dates():
    out = render_complaint(_fields(), TEMPLATE)
    assert "256266.8元（人民币大写：贰拾伍万陆仟贰佰陆拾陆元捌角）" in out
    assert "2023年05月01日" in out
    assert "台数6台" in out
    assert "北京华龙电梯有限公司" in out
    # 无冲突/OCR，不应出现标注
    assert "【高亮冲突" not in out
    assert "待核实" not in out


def test_render_missing_field_marks_placeholder():
    out = render_complaint(_fields(defendant_name={"value": None, "src": ""}), TEMPLATE)
    assert "被告【待补充】" in out
    # 模板中未提供的 court_name 也应为待补充
    assert "法院【待补充】人民法院" in out


def test_render_conflict_amount_is_highlighted():
    # 金额不勾稽 → unpaid_amount 冲突（900000 ≠ 638667.2 + 256266.8）
    out = render_complaint(
        _fields(total_amount={"value": 900000, "src": "《审批表》"}), TEMPLATE
    )
    assert "【高亮冲突：256266.8元（人民币大写：贰拾伍万陆仟贰佰陆拾陆元捌角）】" in out


def test_render_ocr_source_marks_uncertain():
    out = render_complaint(
        _fields(defendant_name={"value": "华龙电梯", "src": "《合同.pdf》（OCR识别，请核实）"}),
        TEMPLATE,
    )
    assert "⚠️ 待核实：华龙电梯" in out


def test_render_qty_conflict_marks_elevator_qty():
    out = render_complaint(
        _fields(elevator_qty_by_acceptance={"value": 5, "src": "《验收报告》"}),
        TEMPLATE,
    )
    assert "【高亮冲突：6】台" in out


def test_render_uses_real_template_when_none():
    # 不传模板时读取 prompts/complaint-template.md，应含标题与落款
    out = render_complaint(_fields())
    assert out.startswith("民事起诉状")
    assert "此致" in out
    assert "具状人：" in out
