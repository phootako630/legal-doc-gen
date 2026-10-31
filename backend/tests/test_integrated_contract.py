# 一体化合同只诉安装款：合同总价写设备款 + 安装款并括注（确认单（六）第 6 题，#140）。虚构金额。
from app.services.complaint_renderer import render_complaint
from app.services.derived_fields import apply_derived_fields
from app.services.extraction import apply_integrated_contract_amounts, find_integrated_prices

# 扫描合同 OCR 成 markdown 表格：设备表在一页，安装表的小计与总计翻到下一页
_PAGE_EQUIP = """一、合同价款
| 序号 | 物资名称 | 数量 | 设备含税单价(元) | 设备不含税总价(元) | 设备税额(元) | 设备总价(元) | 备注 |
| 1 | 客梯 | 2 | 100000 | 176991.15 | 23008.85 | 200000 | 设备税率13% |
| 小计 | / | / | 176991.15 | 23008.85 | 200000 | 设备税率13% |

| 序号 | 物资名称 | 数量 | 安装含税单价(元) | 安装不含税总价(元) | 安装税额(元) | 安装总价(元) | 备注 |
| 1 | 客梯 | 2 | 50000 | 97087.38 | 2912.62 | 100000 |  |"""
_PAGE_INSTALL = """| 小计 | / | / | 97087.38 | 2912.62 | 100000 |  |
| 总计 | / | / | 274078.53 | 25921.47 | 300000 |  |
| 总计金额：大写人民币叁拾万元（小写：￥300000.00元） |"""


def _files(pages=(_PAGE_EQUIP, _PAGE_INSTALL), name="虚构一体化合同.pdf"):
    return [
        {"filename": "虚构审批表（一体化安装）.pdf", "identified_type": "审批表", "text": "审批表"},
        {
            "filename": name, "identified_type": "合同", "is_scanned": True,
            "pages": [{"page": i + 1, "text": t} for i, t in enumerate(pages)],
            "text": "\n\n".join(pages),
        },
    ]


def _fields(total="100,000.00"):
    return {
        "total_amount": {"value": total, "src": "《虚构审批表（一体化安装）.pdf》安装合同总额"},
        "paid_amount": {"value": "60,000.00", "src": "《虚构审批表》"},
        "unpaid_amount": {"value": "40,000.00", "src": "《虚构审批表》"},
    }


def test_prices_found_across_pages():
    found = find_integrated_prices(_files()[1])
    assert found == {"设备": (200000, 1), "安装": (100000, 2), "总计": (300000, 2)}


def test_integrated_total_with_breakdown():
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files())
    assert fields["contract_equipment_amount"]["value"] == 200000
    assert "第2页" in fields["contract_install_amount"]["src"] and "OCR" in fields["contract_install_amount"]["src"]
    f = apply_derived_fields(fields)
    assert f["contract_total_stated"]["value"] == "300000"
    assert "待核实" in f["contract_total_stated"]["src"] and "相差" not in f["contract_total_stated"]["src"]
    assert f["contract_total_breakdown"]["value"] == "（设备款¥200000元 + 安装款¥100000元）"
    text = render_complaint(fields)
    assert "合同总价为¥⚠️ 待核实：300000元⚠️ 待核实：（设备款¥200000元 + 安装款¥100000元）。" in text
    # 诉请仍只针对安装款
    assert "支付剩余合同款¥40000元" in text or "¥40000元" in text


def test_breakdown_cites_amount_sources():
    # 括注沿用两项金额的出处并标待核实（#145 评审）
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files())
    src = apply_derived_fields(fields)["contract_total_breakdown"]["src"]
    assert "设备款：《虚构一体化合同.pdf》第1页" in src and "安装款：《虚构一体化合同.pdf》第2页" in src
    assert src.endswith("待核实")


def test_wan_yuan_table_converted():
    # 价格表以「万元」为单位：换算成元（#145 评审）
    pages = ("单位：万元\n设备总价\n小计 / / 17.699115 2.300885 20\n安装总价\n小计 / / 9.708738 0.291262 10\n总计 / / 27.407853 2.592147 30",)
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files(pages=pages))
    assert fields["contract_equipment_amount"]["value"] == 200000
    assert fields["contract_install_amount"]["value"] == 100000
    assert apply_derived_fields(fields)["contract_total_stated"]["value"] == "300000"


def test_unknown_unit_left_for_lawyer():
    pages = ("设备总价\n小计 / / 17.699115 2.300885 20\n安装总价\n小计 / / 9.708738 0.291262 10\n总计 / / 27.407853 2.592147 30",)
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files(pages=pages))
    assert fields["contract_equipment_amount"]["value"] is None
    assert "单位" in fields["contract_equipment_amount"]["src"]
    f = apply_derived_fields(fields)
    assert f["contract_total_stated"]["value"] == "100,000.00" and "只写安装款" in f["contract_total_stated"]["src"]


def test_missing_grand_total_not_used():
    # 没读到「总计」：两项小计无法核对，不写全部总价（#145 评审）
    pages = (_PAGE_EQUIP, _PAGE_INSTALL.split("\n")[0])
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files(pages=pages))
    assert fields["contract_equipment_amount"]["value"] is None
    assert "未读到「总计」" in fields["contract_equipment_amount"]["src"]
    assert "contract_install_amount" not in fields
    assert apply_derived_fields(fields)["contract_total_breakdown"]["value"] == ""


def test_integrated_marker_deep_in_approval_text():
    # 审批表文件名不含「一体化」，合同类型栏在正文后面：仍识别为一体化，回退时说明原因（#145 评审）
    files = [
        {"filename": "虚构审批表.pdf", "identified_type": "审批表", "text": "说明" * 200 + "合同类型\n一体化安装"},
        {"filename": "虚构合同.pdf", "identified_type": "合同", "text": "合同正文，没有价格表"},
    ]
    fields = _fields()
    apply_integrated_contract_amounts(fields, files)
    assert "未读到" in fields["contract_equipment_amount"]["src"]


def test_install_differs_from_approval_notes_difference():
    fields = _fields(total="98,000.00")
    apply_integrated_contract_amounts(fields, _files())
    src = apply_derived_fields(fields)["contract_total_stated"]["src"]
    assert "审批表合同总额 98000 与合同安装款相差 -2000" in src


def test_integrated_without_price_table_falls_back_to_install():
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files(pages=("合同正文，没有价格表",)))
    f = apply_derived_fields(fields)
    assert f["contract_total_stated"]["value"] == "100,000.00"
    assert "设备款 / 安装款小计未读到" in f["contract_total_stated"]["src"]
    assert "只写安装款" in f["contract_total_stated"]["src"]
    assert f["contract_total_breakdown"]["value"] == ""
    assert "合同总价为¥⚠️ 待核实：100,000.00元。" in render_complaint(fields)


def test_subtotals_not_matching_grand_total_left_for_lawyer():
    pages = (_PAGE_EQUIP, _PAGE_INSTALL.replace("| 总计 | / | / | 274078.53 | 25921.47 | 300000 |", "| 总计 | / | / | 1 | 2 | 310000 |"))
    fields = _fields()
    apply_integrated_contract_amounts(fields, _files(pages=pages))
    assert fields["contract_equipment_amount"]["value"] is None
    assert "不符" in fields["contract_equipment_amount"]["src"]
    assert apply_derived_fields(fields)["contract_total_breakdown"]["value"] == ""


def test_cell_per_line_layout():
    page = "设备总价(元)\n小计\n/\n176991.15\n23008.85\n200000\n安装总价(元)\n小计\n97087.38\n2912.62\n100000\n"
    found = find_integrated_prices({"text": page})
    assert found["设备"][0] == 200000 and found["安装"][0] == 100000


def test_ordinary_contract_unaffected():
    fields = _fields()
    files = [{"filename": "虚构安装合同.pdf", "identified_type": "合同", "text": "安装总价 100000 元"}]
    apply_integrated_contract_amounts(fields, files)
    assert "contract_equipment_amount" not in fields
    f = apply_derived_fields(fields)
    assert f["contract_total_stated"]["value"] == "100,000.00"
    assert f["contract_total_stated"]["src"] == "《虚构审批表（一体化安装）.pdf》安装合同总额"
    assert f["contract_total_breakdown"]["value"] == ""


def test_lawyer_edit_kept():
    fields = _fields()
    fields["contract_equipment_amount"] = {"value": "250000", "src": "律师人工确认修改"}
    apply_integrated_contract_amounts(fields, _files())
    assert fields["contract_equipment_amount"]["value"] == "250000"
