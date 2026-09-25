# complaint_renderer 单测：人民币大写 + 模板填槽 + 代码按状态贴标注
from app.services.complaint_renderer import (
    amount_to_chinese_rmb,
    render_complaint,
)

TEMPLATE = (
    "款{{unpaid_amount}}；签订{{contract_sign_date}}；"
    "台数{{elevator_qty}}台；被告{{defendant_name}}；"
    "法院{{court_name}}人民法院；联系人{{contacts}}；条款{{payment_clause_text}}。"
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
    # 金额只出数字（模板自带 ¥…元），日期不补零——对齐律师诉状模板
    assert "款256266.8；" in out
    assert "2023年5月1日" in out
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
    assert "【高亮冲突：256266.8】" in out


def test_render_ocr_source_marks_uncertain():
    out = render_complaint(
        _fields(
            defendant_name={
                "value": "华龙电梯",
                "src": "《合同.pdf》（OCR识别，请核实）",
            }
        ),
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
    assert out.startswith("民 事 起 诉 状")
    assert "此致" in out
    assert "诉讼请求：" in out and "事实和理由：" in out
    # 未知占位符不应残留
    assert "{{" not in out


def test_render_contacts_list_formatted():
    contacts = [
        {
            "name": {"value": "李四", "src": "《审批表》"},
            "phone": {"value": "13800000000", "src": "《审批表》"},
        },
        {
            "name": {"value": "李四", "src": "《审批表》"},
            "phone": {"value": None, "src": ""},
        },
    ]
    out = render_complaint(_fields(contacts=contacts), TEMPLATE)
    assert "联系人李四 13800000000；李四；" in out


def test_render_contacts_missing_marks_placeholder():
    out = render_complaint(_fields(contacts=[]), TEMPLATE)
    assert "联系人【待补充】；" in out


def test_render_clause_text_trailing_punct_not_doubled():
    out = render_complaint(
        _fields(
            payment_clause_text={"value": "验收后30日内付清。", "src": "《审批表》"}
        ),
        TEMPLATE,
    )
    assert "条款验收后30日内付清。" in out
    assert "。。" not in out


# ── 律师样例全文回归：以律师诉状模板（样例案件，已脱敏）的真实取值渲染 ─────────────
_SAMPLE = {
    "plaintiff_name_final": {
        "value": "某电梯（中国）有限公司江苏分公司",
        "src": "《审批表》合同分公司",
    },
    "plaintiff_credit_code": {"value": "91320100000000000X", "src": "律师填写"},
    "plaintiff_person_in_charge": {"value": "张三，总经理", "src": "律师填写"},
    "plaintiff_address": {
        "value": "江苏省南京市鼓楼区某路1号101室",
        "src": "律师填写",
    },
    "plaintiff_phone": {"value": "025-00000000", "src": "律师填写"},
    "defendant_name": {
        "value": "南京某置业有限公司",
        "src": "《审批表》合同买方名称",
    },
    "defendant_credit_code": {"value": "91320114MA0000000H", "src": "企查查"},
    "defendant_legal_rep": {"value": "王五", "src": "企查查"},
    "defendant_address": {
        "value": "南京市雨花台区某街道某大街1号",
        "src": "企查查",
    },
    "contacts": [
        {
            "name": {"value": "李四", "src": "《审批表》"},
            "phone": {"value": "13800000000", "src": "《审批表》"},
        }
    ],
    "contract_sign_date": {"value": "2024-02-26", "src": "《合同》封面"},
    "contract_title": {
        "value": "南京某某二期电梯安装工程合同",
        "src": "《合同》封面",
    },
    "contract_no": {"value": "AH0000001", "src": "《审批表》"},
    "elevator_qty": {"value": 14, "src": "《审批表》情况说明"},
    "total_amount": {"value": 894934, "src": "《审批表》合同总额"},
    "paid_amount": {"value": 638667.2, "src": "《审批表》已付款"},
    "unpaid_amount": {"value": 256266.8, "src": "《审批表》未付款金额"},
    "acceptance_latest_date": {"value": "2024年10月25日", "src": "《验收报告》"},
    "payment_clause_location": {"value": "第二十八章", "src": "《合同》"},
    "payment_clause_text": {
        "value": "电梯安装完成后，30个工作日内支付合同总价的60%",
        "src": "《合同》",
    },
    "dispute_clause_location": {
        "value": "第二十章第1.1条及第一条第2款",
        "src": "《合同》",
    },
    "dispute_clause_text": {
        "value": "履行合同时发生争议，协商、调解不成的，双方向工程所在地的当地法院提起诉讼。",
        "src": "《合同》",
    },
    "project_site": {
        "value": "江苏省南京市雨花台区某某二期项目",
        "src": "《验收报告》",
    },
}


def test_render_lawyer_sample_matches_template_wording():
    out = render_complaint(_SAMPLE)
    for expected in [
        "原告：某电梯（中国）有限公司江苏分公司",
        "联系人：李四 13800000000",
        "1、判令被告向原告支付剩余合同款¥256266.8元；",
        "2、判令被告向原告支付逾期付款利息（以¥256266.8元为基数，自起诉之日起，"
        "按照全国银行间同业拆借中心公布的贷款市场报价利率计至实际付清之日止）；",
        "2024年2月26日，原、被告双方签订了《南京某某二期电梯安装工程合同》"
        "（合同编号：AH0000001），约定原告负责安装14台电梯，合同总价为¥894934元。",
        "依据合同第二十八章约定，电梯安装完成后，30个工作日内支付合同总价的60%。",
        "案涉合同项下14台电梯均于2024年10月25日前验收合格。",
        # 未付 = 总额 - 已付（律师样例此处误写为 894934-256266.8，这里按正确算式）
        "尚欠剩余合同款¥256266.8元（894934-638667.2）未付",
        "依据案涉合同第二十章第1.1条及第一条第2款约定，履行合同时发生争议，"
        "协商、调解不成的，双方向工程所在地的当地法院提起诉讼。因工程所在地为"
        "江苏省南京市雨花台区某某二期项目，属南京市雨花台区法院辖区，"
        "故原告向南京市雨花台区人民法院提起诉讼。",
        "此致\n南京市雨花台区人民法院",
    ]:
        # 去掉待核实标注后比对正文措辞（导出 Word 时前端同样会去掉标注）
        assert expected in out.replace("⚠️ 待核实：", ""), expected
    # 推定的管辖法院一律标待核实，交律师确认
    assert "属⚠️ 待核实：南京市雨花台区法院辖区" in out
    assert "{{" not in out and "【待补充】" not in out
