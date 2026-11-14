# 管辖判断两处修复（新案卷核对 + 律师校准）：
# 1. 争议条款列了几种解决方式、写明选哪一种时，只按选中的一项判管辖（选的是诉讼，系统误写成仲裁申请书）；
# 2. 「XX市某某小区」里的「小区」不是行政区，不推成「XX市某某小区人民法院」。全部虚构数据。
import pytest

from app.services.derived_fields import apply_derived_fields, derive_court_district, selected_dispute_option

CLAUSE = (
    "17.2 因设备的质量问题发生争议时，由技术质量监督单位进行质量鉴定。除设备的质量问题外，凡与合同履行有关而引起的"
    "一切争议，买卖双方应首先通过友好协商解决，如经协商后仍不能达成协议时，任何一方可以按以下方式 {pick} 解决"
    "(具体项目签约时由甲方选择)：(1)向工程所在地法院提起诉讼；(2)向工程所在地仲裁委员会提请仲裁。"
    "在争议审理期间，合同其他部分仍应继续履行。"
)


def _fields(clause):
    return apply_derived_fields({
        "dispute_clause_text": {"value": clause, "src": "《合同.pdf》第17条"},
        "project_site": {"value": "上海市杨浦区某某路1号", "src": "《合同.pdf》第1条"},
    })


def test_picked_litigation_is_not_arbitration():
    f = _fields(CLAUSE.format(pick="1"))
    assert f["document_kind"]["value"] == "民事起诉状"
    assert f["court_district"]["value"] == "上海市杨浦区"
    assert "人民法院提起诉讼" in f["jurisdiction_text"]["value"]
    assert "仲裁" not in f["jurisdiction_text"]["value"]


def test_picked_arbitration_still_arbitration():
    f = _fields(CLAUSE.format(pick="2"))
    assert f["document_kind"]["value"] == "仲裁申请书"


@pytest.mark.parametrize("pick", ["_1", "__1__", "１", "（1）"])
def test_pick_written_in_blank_or_fullwidth(pick):
    # 手填在下划线空格里的编号（OCR 成「方式_1解决」）、全角数字、带括号，都认得出选的是诉讼
    assert _fields(CLAUSE.format(pick=pick))["document_kind"]["value"] == "民事起诉状"


@pytest.mark.parametrize("pick", ["", "___", "3"])
def test_unpicked_or_unknown_option_left_as_is(pick):
    # 没写选哪项、编号对不上：不猜，原样交给原有规则
    text = CLAUSE.format(pick=pick).replace(" ", "")
    assert selected_dispute_option(text) == text


@pytest.mark.parametrize(
    ("clause", "kept", "dropped"),
    [
        ("任何一方可以按以下方式1解决：(1)向甲方所在地法院提起诉讼；(2)提交某某仲裁委员会仲裁。其他。",
         "(1)向甲方所在地法院提起诉讼", "仲裁"),
        ("双方选择第（二）种方式解决：（一）向乙方所在地人民法院起诉；（二）提交某某仲裁委员会仲裁。",
         "（二）提交某某仲裁委员会仲裁", "人民法院"),
        ("按下列第2项处理：(1)向法院起诉；(2)申请仲裁；(3)其他方式。", "(2)申请仲裁", "向法院起诉"),
    ],
)
def test_selected_option_kept_others_dropped(clause, kept, dropped):
    out = selected_dispute_option(clause)
    assert kept in out and dropped not in out
    assert out.endswith("。") or out.endswith("其他。")


def test_no_options_unchanged():
    text = "因本合同发生的争议，提交某某仲裁委员会仲裁。"
    assert selected_dispute_option(text) == text


@pytest.mark.parametrize(
    ("address", "court"),
    [
        ("山东省肥城市某某名园小区工地", "肥城市"),  # 小区不是区；肥城是县级市
        ("泰安市肥城市某某镇", "肥城市"),
        ("济南市历下区某某小区", "济南市历下区"),  # 真正的市辖区照常
        ("青岛市黄岛区某某社区", "青岛市黄岛区"),
        ("某某市某某花园小区", None),  # 推不出就不推，交律师填
        ("某某市某某校区", None),
        # 「矿区」有真实的市辖区，照常推定（#167 评审）
        ("河北省石家庄市井陉矿区某某路", "石家庄市井陉矿区"),
        ("邯郸市峰峰矿区某某镇", "邯郸市峰峰矿区"),
    ],
)
def test_residential_compound_is_not_district(address, court):
    assert derive_court_district(address) == court



def _with_institution(clause, extracted):
    return apply_derived_fields({
        "dispute_clause_text": {"value": clause, "src": "《合同.pdf》第17条"},
        "arbitration_institution": {"value": extracted, "src": "《合同.pdf》第17条"},
        "project_site": {"value": "上海市杨浦区某某路1号", "src": "《合同.pdf》第1条"},
    })


def test_picked_litigation_ignores_institution_from_unpicked_option():
    """选中的是诉讼（条款没写「法院」二字），抽取的仲裁机构来自没选的一项：不能兜底改成仲裁（#167 评审）。"""
    f = _with_institution("任何一方可以按以下方式1解决：(1)依法提起诉讼；(2)向甲仲裁委员会申请仲裁。", "甲仲裁委员会")
    assert f["document_kind"]["value"] == "民事起诉状"
    assert "仲裁" not in (f["jurisdiction_text"]["value"] or "")


def test_picked_option_names_its_own_institution():
    """选中第 2 项乙仲裁委员会，抽取值却是没选的甲：以选中项为准（#167 评审）。"""
    f = _with_institution(
        "任何一方可以按以下方式2解决：(1)向甲仲裁委员会申请仲裁；(2)向乙仲裁委员会申请仲裁。", "甲仲裁委员会"
    )
    assert f["document_kind"]["value"] == "仲裁申请书"
    assert "乙仲裁委员会" in f["jurisdiction_text"]["value"] and "甲仲裁委员会" not in f["jurisdiction_text"]["value"]
    assert f["addressee"]["value"] == "乙仲裁委员会"


def test_picked_arbitration_without_name_does_not_borrow_unpicked():
    """选中项只写「申请仲裁」、没写机构：抽取值不在选中项里就不用，交律师补（宁缺勿造）。"""
    f = _with_institution(
        "任何一方可以按以下方式2解决：(1)向甲仲裁委员会申请仲裁；(2)向工程所在地有管辖权的机构申请仲裁。",
        "甲仲裁委员会",
    )
    assert f["document_kind"]["value"] == "仲裁申请书"
    assert "甲仲裁委员会" not in f["jurisdiction_text"]["value"]


@pytest.mark.parametrize(
    "clause",
    [
        "双方可诉讼或仲裁，按以下方式1解决：(1)向工程所在地法院起诉；(2)向甲仲裁委员会申请仲裁。",
        "按以下方式1解决：(1)向工程所在地法院起诉；(2)向甲仲裁委员会申请仲裁。仲裁裁决为终局裁决。",
    ],
)
def test_picked_litigation_ignores_arbitration_words_outside_option(clause):
    """选中诉讼时只看选中项：前言「诉讼或仲裁」、附文「仲裁裁决为终局」里的「仲裁」不改文书类型（#167 复审）。"""
    f = _with_institution(clause, None)
    assert f["document_kind"]["value"] == "民事起诉状"


@pytest.mark.parametrize("body", ["乙仲裁院", "乙国际仲裁中心"])
def test_picked_option_institution_other_suffixes(body):
    """选中项写的是仲裁院 / 仲裁中心，也能截出来，不借用没选的那一项（#167 复审）。"""
    f = _with_institution(f"按以下方式2解决：(1)向甲仲裁院申请仲裁；(2)向{body}申请仲裁。", "甲仲裁院")
    assert f["addressee"]["value"] == body
    assert body in f["jurisdiction_text"]["value"]


@pytest.mark.parametrize("extracted", ["北京某某仲裁委员会", None])
def test_picked_institution_not_swallow_leading_words(extracted):
    """抽取值在选中项里就用它；没有抽取值时截取不把「争议应由」吞进机构名（#167 复审）。"""
    f = _with_institution(
        "按以下方式1解决：(1)争议应由北京某某仲裁委员会仲裁；(2)向工程所在地法院起诉。", extracted
    )
    assert f["addressee"]["value"] == "北京某某仲裁委员会"
