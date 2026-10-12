# Codex 复审发现的三处问题的回归测试（#29 交货地、#30 违约金、#31 职务）。数据均为虚构。
import json
from datetime import date

import pytest

from app.config import PLAINTIFF_HQ_NAME as HQ
from app.services import branch_registry, derived_fields
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields


def _f(**kw):
    return {k: {"value": v, "src": "《审批表》"} for k, v in kw.items()}


# ── #29：交货地点缺失时不拿工程地点冒充 ─────────────────────────────────────

_DELIVERY = "向交货地点所在地人民法院起诉"


def test_missing_delivery_place_infers_from_install_address_not_project_site():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_DELIVERY,
            project_site="南京市甲区某路",
            install_address="苏州市乙区某路",
        )
    )
    court = fields["court_district"]
    assert court["value"] == "苏州市乙区"
    assert "合同交货地点未识别" in court["src"]
    assert "验收报告安装地点「苏州市乙区某路」推断" in court["src"]
    text = fields["jurisdiction_text"]["value"]
    assert "南京" not in text
    assert text == (
        "因交货地点为【待补充】，属苏州市乙区法院辖区"
        "【系统按验收报告安装地点「苏州市乙区某路」推断，待核实】，"
        "故原告向苏州市乙区人民法院提起诉讼。"
    )
    # 律师在最终文书里就能看到法院是系统推断及其依据
    rendered = render_complaint(fields, template="{{jurisdiction_text}}")
    assert rendered.startswith("⚠️ 待核实：") and "南京" not in rendered
    assert "系统按验收报告安装地点「苏州市乙区某路」推断" in rendered


def test_missing_delivery_place_without_install_address_is_left_blank():
    fields = apply_derived_fields(
        _f(dispute_clause_text=_DELIVERY, project_site="南京市甲区某路")
    )
    assert fields["court_district"]["value"] is None
    assert fields["addressee"]["value"] == "【待补充】人民法院"


def test_delivery_place_with_district_wins_over_other_places():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text=_DELIVERY,
            delivery_place="无锡市丙区某工地",
            project_site="南京市甲区某路",
            install_address="苏州市乙区某路",
        )
    )
    assert fields["court_district"]["value"] == "无锡市丙区"
    text = fields["jurisdiction_text"]["value"]
    assert "因交货地点为无锡市丙区某工地" in text and "推断" not in text


def test_project_site_without_district_also_notes_inference():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地人民法院起诉",
            project_site="某某花园项目",
            install_address="苏州市乙区某路",
        )
    )
    assert (
        "【系统按验收报告安装地点「苏州市乙区某路」推断，待核实】"
        in (fields["jurisdiction_text"]["value"])
    )


def test_lawyer_set_court_is_kept():
    fields = _f(dispute_clause_text=_DELIVERY, install_address="苏州市乙区某路")
    fields["court_district"] = {"value": "常州市丁区", "src": "律师人工确认修改"}
    fields = apply_derived_fields(fields)
    assert fields["court_district"]["value"] == "常州市丁区"
    assert "推断" not in fields["jurisdiction_text"]["value"]


def test_project_site_jurisdiction_unchanged():
    fields = apply_derived_fields(
        _f(
            dispute_clause_text="向工程所在地人民法院起诉",
            project_site="南京市甲区某路",
        )
    )
    assert fields["court_district"]["value"] == "南京市甲区"
    assert fields["jurisdiction_text"]["value"].startswith(
        "因工程所在地为南京市甲区某路"
    )


# ── #30：固定 / 一次性违约金直接请求约定金额（确认单四第 1 题）───────────────


def _penalty(clause: str, rate: str | None = None) -> dict:
    fields = _f(
        total_amount=200000,
        paid_amount=100000,
        unpaid_amount=100000,
        breach_interest_clause_location="第11条",
        breach_clause_text=clause,
    )
    if rate:
        fields["breach_interest_rate_text"] = {"value": rate, "src": "《合同》第11条"}
    return fields


# 律师确认单（四）第 1 题：诉请直接写违约金金额，不写「以……为基数……计至实际付清之日止」
@pytest.mark.parametrize(
    ("clause", "rate", "amount"),
    [
        ("甲方逾期付款的，应向乙方支付违约金人民币5000元", None, "5000"),
        ("甲方逾期付款的，应向乙方支付违约金", "人民币5,000.00元", "5000"),
        ("甲方逾期付款的，应向乙方支付违约金1.5万元", None, "15000"),
        # 「30日」是触发期限，不是计算周期
        ("甲方逾期付款的，应支付违约金", "逾期超过30日一次性支付5000元违约金", "5000"),
        ("甲方逾期付款的，应按合同总价的10%向乙方支付违约金", None, "20000"),
        (
            "甲方逾期付款的，应支付违约金",
            "逾期超过30日按合同总价的10%一次性支付违约金",
            "20000",
        ),
        ("甲方逾期付款的，应按欠款的5%支付违约金", None, "5000"),
    ],
)
def test_lump_sum_penalty_claims_the_amount(clause, rate, amount):
    fields = _penalty(clause, rate=rate)
    text = render_complaint(fields)
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert claim == f"2、判令被告向原告支付违约金⚠️ 待核实：¥{amount}元；"
    assert "被告应向原告支付违约金⚠️ 待核实：¥" in text
    assert "计至实际付清之日止" not in text and "贷款市场报价利率" not in text
    node = apply_derived_fields(fields)["penalty_amount"]
    assert "待核实" in node["src"]  # 提醒办案律师核对金额


@pytest.mark.parametrize(
    "clause",
    [
        "甲方逾期付款的，应支付违约金",  # 没写金额
        "逾期30日内支付违约金1000元，超过30日支付违约金3000元",  # 分档，分不清
        "按合同总价的10%支付违约金",  # 比例，但缺合同总价
    ],
)
def test_lump_sum_penalty_amount_unknown_is_left_blank(clause):
    fields = _penalty(clause)
    if "合同总价" in clause:
        fields.pop("total_amount")
    text = render_complaint(fields)
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert claim == "2、判令被告向原告支付违约金【待补充】；"


@pytest.mark.parametrize(
    "clause",
    [
        # 分档：两个比例
        "逾期30日内按合同总价的5%支付违约金，超过30日按合同总价的10%支付违约金",
        # 比例 + 上限
        "按合同总价的10%支付违约金，但最高不超过5000元",
        # 比例基数识别不了 + 上限金额：不能把上限当固定金额
        "按未支付的安装价款的5%支付违约金，但最高不超过5000元",
        # 比例基数识别不了
        "按结算价的5%支付违约金",
        # 固定金额 + 上限
        "支付违约金5000元，累计不超过合同总价的3%",
        # 比例数值相同、基数不同的两档
        "逾期30日内按欠款的5%支付违约金，超过30日按合同总价的5%支付违约金",
    ],
)
def test_ambiguous_lump_sum_penalty_is_left_blank(clause):
    fields = _penalty(clause)
    text = render_complaint(fields)
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert claim == "2、判令被告向原告支付违约金【待补充】；"
    node = apply_derived_fields(fields)["penalty_amount"]
    assert node["value"] is None and "不自动计算" in node["src"]


def test_same_standard_quoted_twice_is_not_tiered():
    # 利率摘录与条款原文写的是同一个标准（基数写法略不同）：不算分档，照常计算
    fields = _penalty(
        "甲方逾期付款的，应按合同总价款的10%向乙方支付违约金", rate="合同总价的10%"
    )
    node = apply_derived_fields(fields)["penalty_amount"]
    assert node["value"] == 20000


@pytest.mark.parametrize(
    ("clause", "rate"),
    [
        ("甲方每逾期一日支付违约金100元", None),
        ("甲方逾期付款的，应支付违约金", "每日100元的违约金标准"),
        ("甲方逾期付款的，按每日500元向乙方支付违约金", None),
        ("甲方逾期付款的，违约金按100元/日计算", None),
        ("甲方逾期付款的，违约金按100元每天计算", None),
        ("甲方每逾期1天，应支付违约金100元", None),
        # 「一次性结清」是付款方式，不是只计一次
        ("甲方逾期付款的，违约金按每日100元计算，付款时一次性结清", None),
    ],
)
def test_per_period_fixed_amount_is_not_lump_sum(clause, rate):
    # 「每日100元」是按日累计，不能当成一次性总额；按补充答复 Q6 写起算日与暂计日
    fields = _penalty(clause, rate=rate)
    derived = apply_derived_fields(fields)
    assert "不适用" in derived["interest_rate_basis"]["value"]
    text = render_complaint(fields)
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert "按照⚠️ 待核实：每日" in claim and "计至实际付清之日止，暂计至" in claim
    # 测试数据没有验收日期：起算日推不出，金额留【待补充】，不派生一次性 100 元
    assert claim.startswith("2、判令被告向原告支付违约金【待补充】（自【待补充】起")
    assert "贷款市场报价利率" not in text


def test_lump_sum_penalty_in_arbitration():
    fields = _penalty("甲方逾期付款的，应向乙方支付违约金人民币5000元")
    fields["dispute_clause_text"] = {
        "value": "提交南京仲裁委员会仲裁",
        "src": "《合同》",
    }
    text = render_complaint(fields)
    assert "裁决被申请人向申请人支付违约金⚠️ 待核实：¥5000元；" in text
    assert "自申请仲裁之日起" not in text  # 计算期间整段换掉了


def test_periodic_clause_without_extracted_rate_is_left_blank():
    # 条款按日计违约金，但费率没抽出来：既不当固定金额，也不套用 LPR
    fields = _penalty("甲方逾期付款的，每日按未付款的万分之五支付违约金")
    text = render_complaint(fields)
    assert "按照【待补充】计至实际付清之日止" in text
    assert "贷款市场报价利率" not in text


def test_lawyer_set_basis_keeps_periodic_wording():
    fields = _penalty("甲方逾期付款的，应向乙方支付违约金人民币5000元")
    fields["interest_rate_basis"] = {
        "value": "日万分之三的标准",
        "src": "律师人工确认修改",
    }
    text = render_complaint(fields)
    assert "按照日万分之三的标准计至实际付清之日止" in text


@pytest.mark.parametrize(
    "rate",
    [
        "日万分之五的利率",
        "年利率6%的标准",
        "每逾期一日按未付款的0.05%",
        "按日0.5‰的标准",
    ],
)
def test_periodic_penalty_keeps_agreed_rate(rate):
    fields = _penalty("甲方逾期付款的，按约定支付违约金", rate=rate)
    text = render_complaint(fields)
    assert f"按照⚠️ 待核实：{rate}计至实际付清之日止" in text
    assert "自起诉之日起" in text


def test_plain_interest_still_defaults_to_lpr():
    text = render_complaint(_f(unpaid_amount=100000))
    assert "支付逾期付款利息" in text and "贷款市场报价利率" in text


# ── 补充答复 Q6：按日计付的固定金额违约金 ───────────────────────────────────

_TODAY = date(2026, 10, 12)
_RETENTION_EXAMPLE = "预留合同金额5%的质保金，质保期满后无息返还"


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(derived_fields, "_today", lambda: _TODAY)


def _daily(**extra) -> dict:
    fields = _penalty("甲方逾期付款的，每逾期一日应向乙方支付违约金100元")
    fields["acceptance_latest_date"] = {
        "value": "2023年11月19日",
        "src": "《检验报告》",
    }
    for k, v in extra.items():
        fields[k] = {"value": v, "src": "《合同》"}
    return fields


def test_daily_penalty_for_retention_follows_lawyer_example(today):
    # 律师例：欠款为质保金，保修二年，最晚验收 2023-11-19 → 2025-11-20 起算
    fields = _daily(
        retention_unpaid_amount="100000.00元",
        warranty_period="二年",
        retention_clause_text=_RETENTION_EXAMPLE,
    )
    derived = apply_derived_fields(fields)
    assert derived["penalty_start_date"]["value"] == "2025-11-20"
    days = (_TODAY - date(2025, 11, 20)).days + 1  # 头尾都算
    assert derived["penalty_amount"]["value"] == 100 * days
    assert f"{days}天" in derived["penalty_amount"]["src"]
    text = render_complaint(fields)
    claim = next(line for line in text.splitlines() if line.startswith("2、"))
    assert claim == (
        f"2、判令被告向原告支付违约金⚠️ 待核实：¥{100 * days}元（自⚠️ 待核实：2025年11月20日起，"
        "按照⚠️ 待核实：每日100元计至实际付清之日止，暂计至2026年10月12日）；"
    )


@pytest.mark.parametrize(
    ("clause", "start"),
    [
        ("电梯验收合格后30日内，甲方支付至合同总价的100%", "2023-12-20"),
        ("验收合格之日起两个月内付清余款", "2024-01-20"),
    ],
)
def test_daily_penalty_start_from_payment_term(today, clause, start):
    fields = _daily(payment_clause_text=clause)
    assert apply_derived_fields(fields)["penalty_start_date"]["value"] == start


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"payment_clause_text": "验收合格后30个工作日内付款"}, "工作日"),
        (
            {"payment_clause_text": "验收合格后30日内付60%，验收合格后90日内付清"},
            "多个付款节点",
        ),
        # 逗号连接的不同节点（#37 复审）
        (
            {"payment_clause_text": "合同签订后30日内支付20%，验收合格后30日内支付80%"},
            "多个付款节点",
        ),
        # 前置条件单独成句
        (
            {"payment_clause_text": "验收合格后30日内付款。付款前乙方须开具发票"},
            "前置条件",
        ),
        ({"payment_clause_text": "完成移交并办理结算后支付"}, "前置条件"),
        # 多个不同节点：不知道欠款属于哪一期（#37 复审）
        (
            {"payment_clause_text": "验收合格后30日内付20%；验收合格满一年后30日内付剩余款"},
            "多个付款节点",
        ),
        # 多节点且含移交 / 结算：前置条件检查先命中
        (
            {
                "payment_clause_text": "验收合格后30日内付20%；完整移交并结算后60日内付剩余款"
            },
            "前置条件",
        ),
        ({"payment_clause_text": "验收合格后30日内并收到发票后付款"}, "前置条件"),
        ({"retention_unpaid_amount": "100000"}, "质保期"),
    ],
)
def test_daily_penalty_start_unknown_is_left_blank(today, extra, reason):
    fields = _daily(**extra)
    derived = apply_derived_fields(fields)
    assert derived["penalty_start_date"]["value"] is None
    assert reason in derived["penalty_start_date"]["src"]
    assert derived["penalty_amount"]["value"] is None


def test_daily_penalty_not_yet_started(today):
    fields = _daily(
        retention_unpaid_amount="100000",
        warranty_period="五年",
        retention_clause_text=_RETENTION_EXAMPLE,
    )
    derived = apply_derived_fields(fields)
    assert derived["penalty_start_date"]["value"] == "2028-11-20"
    assert derived["penalty_amount"]["value"] is None
    assert "尚未产生" in derived["penalty_amount"]["src"]


@pytest.mark.parametrize(
    "clause",
    [
        "预留5%质保金，质保期满后30日内返还",
        "质保期满后30日返还质保金",
        "质保期满后的30日内返还质保金",
        "质保期满后30个日历日内返还",
    ],
)
def test_retention_grace_written_variously(today, clause):
    fields = _daily(
        retention_unpaid_amount="100000",
        warranty_period="二年",
        retention_clause_text=clause,
    )
    assert apply_derived_fields(fields)["penalty_start_date"]["value"] == "2025-12-20"


def test_retention_grace_in_months(today):
    fields = _daily(
        retention_unpaid_amount="100000",
        warranty_period="二年",
        retention_clause_text="质保期满后三个月返还",
    )
    assert apply_derived_fields(fields)["penalty_start_date"]["value"] == "2026-02-20"


def test_retention_grace_period_is_added(today):
    # 「质保期满后30日内返还」：到期日 = 质保期满 + 30 日，起算日再加一天（#37 复审）
    fields = _daily(
        retention_unpaid_amount="100000",
        warranty_period="二年",
        retention_clause_text="预留5%质保金，质保期满后30日内返还",
    )
    assert apply_derived_fields(fields)["penalty_start_date"]["value"] == "2025-12-20"


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        # 质保期自交付使用起算，不能按验收日算
        (
            {
                "retention_clause_text": _RETENTION_EXAMPLE,
                "_period_src": "《合同》第8条，自交付使用之日起",
            },
            "不是自验收",
        ),
        # 没写返还条件
        ({}, "返还条件"),
        # 分期返还
        ({"retention_clause_text": "质保期满一年返还2%，质保期满后返还剩余3%"}, "分期"),
        # 带结算前置条件
        ({"retention_clause_text": "质保期满并办理结算后返还"}, "前置条件"),
        # 按工作日
        ({"retention_clause_text": "质保期满后30个工作日内返还"}, "工作日"),
        # 写了期限但系统解析不出：不能当作期满即返还（#37 复审）
        ({"retention_clause_text": "质保期满三十天后，甲方返还质保金"}, "无法确定"),
        ({"retention_clause_text": "质保期满，经甲方确认后十五日内返还"}, "无法确定"),
        # 按年 / 按周的返还期限：两个正则都没认出也不能当零天（#37 复审）
        ({"retention_clause_text": "质保期满后一年内返还质保金"}, "无法确定"),
        ({"retention_clause_text": "质保期满一年后无息返还"}, "无法确定"),
        ({"retention_clause_text": "质保期满后两周内返还"}, "无法确定"),
        ({"retention_clause_text": "质保期满后一个季度内返还"}, "无法确定"),
    ],
)
def test_retention_due_unclear_is_left_blank(today, extra, reason):
    extra = dict(extra)
    period_src = extra.pop("_period_src", "《合同》第8条")
    fields = _daily(retention_unpaid_amount="100000", **extra)
    fields["warranty_period"] = {"value": "二年", "src": period_src}
    derived = apply_derived_fields(fields)
    assert derived["penalty_start_date"]["value"] is None
    assert reason in derived["penalty_start_date"]["src"]
    assert derived["penalty_amount"]["value"] is None


def _lawyer(value: str) -> dict:
    return {"value": value, "src": "律师人工确认修改"}


def test_amount_follows_lawyer_edited_inputs(today):
    # #37 复审复现：律师改每日标准、起算日、暂计日后，金额按律师的值重算
    fields = _daily(payment_clause_text="验收合格后30日内付清")
    fields["acceptance_latest_date"] = {"value": "2026年8月5日", "src": "《检验报告》"}
    fields["penalty_daily_text"] = _lawyer("每日200元")
    fields["penalty_start_date"] = _lawyer("2026年10月1日")
    fields["penalty_until_date"] = _lawyer("2026年10月5日")
    derived = apply_derived_fields(fields)
    assert derived["penalty_amount"]["value"] == 1000  # 200 × 5 天
    text = render_complaint(fields)
    assert "¥1000元（自2026年10月1日起，按照每日200元" in text
    assert "暂计至2026年10月5日" in text


def test_lawyer_filled_start_date_enables_amount(today):
    # 规则推不出起算日，律师补上后金额随之算出
    fields = _daily(payment_clause_text="验收合格后30个工作日内付款")
    fields["penalty_start_date"] = _lawyer("2026-10-03")
    assert (
        apply_derived_fields(fields)["penalty_amount"]["value"] == 1000
    )  # 100 × 10 天


def test_lawyer_bare_number_rate_and_unreadable_rate(today):
    fields = _daily(payment_clause_text="验收合格后30日内付清")
    fields["penalty_daily_text"] = _lawyer("150")
    days = (_TODAY - date(2023, 12, 20)).days + 1
    assert apply_derived_fields(fields)["penalty_amount"]["value"] == 150 * days
    fields["penalty_daily_text"] = _lawyer("按约定标准")
    node = apply_derived_fields(fields)["penalty_amount"]
    assert node["value"] is None and "按日违约金标准" in node["src"]


def test_lawyer_edited_amount_is_kept(today):
    fields = _daily(payment_clause_text="验收合格后30日内付清")
    fields["penalty_amount"] = _lawyer("8888")
    assert apply_derived_fields(fields)["penalty_amount"]["value"] == "8888"


def test_monthly_fixed_amount_is_still_left_blank():
    # 按月计的固定金额律师未确认写法：不进入按日计算，计算标准仍留【待补充】
    fields = _penalty("甲方每逾期一个月应支付违约金1000元")
    derived = apply_derived_fields(fields)
    assert derived["interest_rate_basis"]["value"] is None
    assert derived.get("penalty_amount", {}).get("value") is None


# ── #31：信息表已写职务时不再追加默认职务 ───────────────────────────────────


@pytest.fixture
def table(tmp_path, monkeypatch):
    def write(rep: str, name: str = HQ):
        path = tmp_path / "branch_info.json"
        entry = {
            "name": name,
            "credit_code": "X",
            "representative": rep,
            "address": "a",
        }
        path.write_text(json.dumps({"entries": [entry]}, ensure_ascii=False), "utf-8")
        monkeypatch.setattr(branch_registry, "BRANCH_INFO_PATH", str(path))

    return write


def _hq_rep() -> dict:
    return apply_derived_fields(_f(contract_type="买卖合同"))[
        "plaintiff_person_in_charge"
    ]


@pytest.mark.parametrize(
    "rep",
    [
        "张三，总经理",
        "张三,总经理",
        "张三、总经理",
        "张三 总经理",
        "张三（总经理）",
        "张三(总经理)",
        "张三，副董事长",  # 比较完整职务，不按子串
        "张三，董事长兼总经理",
    ],
)
def test_existing_title_is_kept_and_flagged(table, rep):
    table(rep)
    node = _hq_rep()
    assert node["value"] == rep and not node["value"].endswith("，董事长")
    assert "待核实" in node["src"] and "《原告信息表》" in node["src"]
    text = render_complaint(
        apply_derived_fields(_f(contract_type="买卖合同")),
        template="{{plaintiff_person_in_charge}}",
    )
    assert text == f"⚠️ 待核实：{rep}"


@pytest.mark.parametrize("rep", ["张三，董事长", "张三（董事长）", "张三 董事长"])
def test_existing_default_title_not_duplicated(table, rep):
    table(rep)
    node = _hq_rep()
    assert node["value"] == rep and "待核实" not in node["src"]


@pytest.mark.parametrize("rep", ["網谷 憲晴", "张三 监事", "张三/董事长"])
def test_ambiguous_format_is_kept_and_flagged(table, rep):
    # 分不清是带空格的姓名还是「姓名 职务」：保留原文、不追加，提醒律师
    table(rep)
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    node = fields["plaintiff_person_in_charge"]
    assert node["value"] == rep
    assert "无法判断" in node["src"] and "待核实" in node["src"]
    again = apply_derived_fields(fields)
    assert again["plaintiff_person_in_charge"]["value"] == rep


@pytest.mark.parametrize("rep", ["张三", "網谷憲晴"])
def test_name_only_gets_default_title_idempotently(table, rep):
    table(rep)
    fields = apply_derived_fields(_f(contract_type="买卖合同"))
    assert fields["plaintiff_person_in_charge"]["value"] == f"{rep}，董事长"
    again = apply_derived_fields(fields)
    assert again["plaintiff_person_in_charge"]["value"] == f"{rep}，董事长"


def test_branch_gets_no_default_title(table):
    table("李四", name=HQ + "甲分公司")
    fields = apply_derived_fields(
        _f(contract_type="安装合同", plaintiff_branch_raw="集团/甲分公司")
    )
    assert fields["plaintiff_person_in_charge"]["value"] == "李四"


def test_lawyer_edited_rep_is_kept(table):
    table("张三")
    fields = _f(contract_type="买卖合同")
    fields["plaintiff_person_in_charge"] = {"value": "王五", "src": "律师人工确认修改"}
    assert apply_derived_fields(fields)["plaintiff_person_in_charge"]["value"] == "王五"
