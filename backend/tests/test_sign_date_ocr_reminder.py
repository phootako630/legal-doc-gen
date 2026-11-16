# 签约日期疑似识别有误的提醒（新案件核对：日期戳「2020-08-03」的 8 盖得淡，扫描识别读成 03）。全部虚构数据。
from app.services.extraction import approval_sign_date
from app.services.validators import check_sign_date_ocr

_OCR_SRC = "《虚构安装合同.pdf》第13页盖章页签署处（OCR识别，请核实）"


def test_one_confusable_digit_off_from_approval_reminds():
    c = check_sign_date_ocr("2020年3月3日", _OCR_SRC, "2020-08-03", "《审批表.pdf》第1页「签约时间」")
    # 只提醒（橙色），不算冲突、不暂停
    assert c.needs_review and c.applicable and c.passed and not c.is_conflict
    assert "3 / 8" in c.message and "签章页原件" in c.message


def test_no_reminder_when_dates_differ_otherwise_or_not_ocr():
    # 两个日期本就常常不同（审批表记的是别的日子）：差不止一位、或差的不是易混数字，都不提醒
    for sign, approval in (("2021年12月15日", "2021-12-29"), ("2024年6月19日", "2024-05-28"), ("2020年3月4日", "2020-03-03")):
        c = check_sign_date_ocr(sign, _OCR_SRC, approval)
        assert not c.applicable and not c.needs_review, (sign, approval)
    # 签约日期不是扫描识别的（文字版合同）：不比对
    c = check_sign_date_ocr("2020年3月3日", "《虚构安装合同.pdf》第5页", "2020-08-03")
    assert not c.applicable
    # 审批表没有签约时间
    assert not check_sign_date_ocr("2020年3月3日", _OCR_SRC, None).applicable


def test_approval_sign_date_read_from_text():
    files = [{
        "filename": "虚构审批表.pdf", "identified_type": "审批表", "text": "",
        "pages": [{"page": 1, "text": "合同号\nAH9000099\n签约时间\n2020-08-03\n验收时间\n-"}],
    }]
    assert approval_sign_date(files) == ("2020-08-03", "《虚构审批表.pdf》第1页「签约时间」")
    files[0]["pages"] = [{"page": 1, "text": "合同类型* 安装合同 签约时间* 2021/1/5"}]
    assert approval_sign_date(files)[0] == "2021-01-05"
    # 两份审批表：对应不上本案合同，不取
    assert approval_sign_date(files + [dict(files[0], filename="另一份审批表.pdf")]) is None
