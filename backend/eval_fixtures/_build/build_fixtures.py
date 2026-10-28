"""生成虚构案卷集的材料 PDF（#82）。所有当事人、金额、地址、编号都是虚构的。

用法（在 backend/ 目录下）：python eval_fixtures/_build/build_fixtures.py
生成的 PDF 已提交进仓库；改了这里的文字要重新生成，并用 --mode live 重录该案件的缓存
（扫描件按页面图片哈希缓存，图片一变缓存就对不上）。
"""
from __future__ import annotations

import os

import fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FONT = "china-s"


def _text_pdf(path: str, pages: list[str]) -> None:
    """文字版 PDF：每页一个文本框，自动换行。"""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 545, 800), text.strip(), fontname=_FONT, fontsize=10.5)
    doc.save(path, garbage=4, deflate=True)


def _draw_table(page: fitz.Page, top: float, rows: list[list[str]]) -> None:
    """画一张带框线的表格（设备清单），扫描后 OCR 会识别成表格。"""
    widths = [60, 140, 140, 80]
    for r, cells in enumerate(rows):
        y = top + r * 28
        x = 60
        for w, cell in zip(widths, cells):
            page.draw_rect(fitz.Rect(x, y, x + w, y + 28), color=(0, 0, 0), width=0.8)
            page.insert_text((x + 8, y + 19), cell, fontname=_FONT, fontsize=12)
            x += w


def _scanned_pdf(path: str, pages: list[str | tuple[str, list[list[str]]]]) -> None:
    """模拟扫描件：先排版成文字页（可带一张表格），再渲染成图片放进新 PDF（没有文字层，只能 OCR）。"""
    src = fitz.open()
    for item in pages:
        text, table = item if isinstance(item, tuple) else (item, None)
        page = src.new_page()
        page.insert_textbox(fitz.Rect(60, 60, 535, 790), text.strip(), fontname=_FONT, fontsize=12)
        if table:
            _draw_table(page, 260, table)
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=120, colorspace=fitz.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("png"))
    out.save(path, garbage=4, deflate=True)


def _approval(case: str, filename: str, v: dict) -> None:
    """仿 KA 诉讼审批表：标签与取值各占一行（与真实审批表 PDF 提取出的文字形态一致）。"""
    rows = [
        ("收款不良合同提起诉讼/仲裁审批表", None),
        ("编号", v["serial"]),
        ("标题", v["title"]),
        ("合同号", v["contract_no"]),
        ("合同分类", "工程合同"),
        ("合同分公司", v["branch_path"]),
        ("合同买方名称", v["buyer"]),
        ("法定代表人", ""),
        ("联系人", v["contact"]),
        ("买方单位地址", v["buyer_address"]),
        ("联系电话", v["contact_phone"]),
        ("电梯安装地点", v["site"]),
        ("合同相关信息", None),
        ("合同类型", v["contract_type"]),
        ("签约时间", v["sign_date"]),
        ("签约台数", v["qty"]),
        ("实际履行台数", v["qty_actual"]),
        ("未付款总金额", v["unpaid"]),
        (f"{v['amount_label']}合同总额", v["total"]),
        ("已付款金额", v["paid"]),
        ("未付款金额", v["unpaid"]),
        ("未付款项构成", None),
        ("质保金", v.get("retention", "0.00元")),
        ("政府验收时间", v["gov_accept"]),
        ("最后一次付款时间", v["last_pay_date"]),
        ("申请原因", v["reason"]),
    ]
    lines = []
    for label, value in rows:
        lines.append(label)
        if value is not None:
            lines.append(str(value) if value != "" else "-")
    _text_pdf(os.path.join(ROOT, case, "materials", filename), ["\n".join(lines)])


def _report(case: str, filename: str, v: dict) -> None:
    """仿电梯监督检验报告：封面（报告编号、设备代码）+ 首页表格（设备品种、安装地点）+ 结论页（批准日期）。"""
    cover = f"""报告编号：{v['report_no']}
曳引驱动乘客电梯监督检验报告
使用单位名称：
{v['user']}
施工单位名称：
{v['installer']}
设 备 代 码：
{v['device_code']}
施 工 类 别：
安装
检 验 日 期：
{v['inspect_date']}
虚构市特种设备检验研究院"""
    first = f"""曳引驱动乘客电梯监督检验报告
报告编号：{v['report_no']}
使用单位名称
{v['user']}
安装地点
{v['site']}
设备品种
曳引驱动乘客电梯
产品型号
{v['model']}
设备代码
{v['device_code']}
施工类别
安装
检验结论
合格"""
    conclusion = f"""检验结论：合格
检验:
日期: {v['inspect_date']}
审核:
日期: {v['inspect_date']}
批  准：虚构检验员  日期：{v['approve_date']}
（检验机构公章或者检验专用章）"""
    _text_pdf(os.path.join(ROOT, case, "materials", filename), [cover, first, conclusion])


def case_install_scanned() -> None:
    """扫描合同按需 OCR、质保金断点、多份文字版验收报告取最晚批准日期。"""
    case = "虚构安装案A"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000001南京虚构置业有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000101", "title": "AH9000001南京虚构置业有限公司虚构花园项目",
        "contract_no": "AH9000001", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "南京虚构置业有限公司", "contact": "虚构乙", "contact_phone": "13800000001",
        "buyer_address": "南京市雨花台区虚构路66号", "site": "南京市雨花台区虚构路88号虚构花园",
        "contract_type": "安装合同", "amount_label": "安装", "sign_date": "2024-03-01", "qty": "3", "qty_actual": "3",
        "total": "300,000.00", "paid": "270,000.00", "unpaid": "30,000.00", "retention": "30,000.00元",
        "gov_accept": "2024-11-10", "last_pay_date": "2024-12-20", "reason": "质保期已满，买方未返还质保金，多次催收未果。",
    })
    _scanned_pdf(os.path.join(ROOT, case, "materials", "南京虚构花园项目电梯安装合同.pdf"), [
        """电梯安装合同

合同编号：AH9000001
项目名称：南京虚构花园项目
甲方（发包方）：南京虚构置业有限公司
乙方（安装方）：日立电梯（中国）有限公司梧桐分公司

甲乙双方经友好协商，就南京虚构花园项目电梯安装工程事宜，订立本合同。""",
        """第一条 工程概况
1.1 工程名称：南京虚构花园项目电梯安装工程。
1.2 工程地点：南京市雨花台区虚构路88号。
1.3 乙方负责安装电梯共3台，型号见附件设备清单。

第二条 合同价款
2.1 本合同安装总价为人民币300,000元（大写：叁拾万元整），含税。""",
        """第三条 付款方式
3.1 电梯设备进场后15日内，甲方支付合同价款的30%。
3.2 电梯安装完毕后15日内，甲方支付至合同价款的60%。
3.3 电梯经政府验收合格后30日内，甲方支付至合同价款的90%。
3.4 合同价款的10%作为质保金，质保期自验收合格之日起24个月，质保期满后30日内甲方无息返还质保金。""",
        """第四条 违约责任
4.1 甲方逾期付款的，每逾期一日，按应付未付款的万分之五向乙方支付违约金。
4.2 乙方逾期完工的，每逾期一日按合同价款的万分之三向甲方支付违约金。

第五条 争议解决
因本合同引起的争议，双方应协商解决；协商不成的，任何一方均可向工程所在地人民法院提起诉讼。""",
        """第六条 其他
6.1 本合同一式四份，甲乙双方各执两份。
6.2 本合同自双方盖章之日起生效。
（以下无正文）

甲方（盖章）：南京虚构置业有限公司
乙方（盖章）：日立电梯（中国）有限公司梧桐分公司
签订日期：2024年3月5日""",
    ])
    for i, (code, approve) in enumerate(
        [("31103201202400000011", "2024年11月12日"), ("31103201202400000012", "2024年11月18日"),
         ("31103201202400000013", "2024年11月15日")], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-90{i:02d}", "user": "南京虚构置业有限公司",
            "installer": "日立电梯（中国）有限公司梧桐分公司", "device_code": code,
            "inspect_date": "2024年11月6日", "approve_date": approve,
            "site": "南京市雨花台区虚构路88号虚构花园1栋", "model": "HGP",
        })


def case_sales_arbitration() -> None:
    """买卖合同争议条款转引战略合作协议，协议约定仲裁 → 仲裁申请书。"""
    case = "虚构买卖案B"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000002苏州虚构商业管理有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000102", "title": "AH9000002苏州虚构商业管理有限公司虚构广场项目",
        "contract_no": "AH9000002", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "苏州虚构商业管理有限公司", "contact": "虚构丙", "contact_phone": "13800000002",
        "buyer_address": "苏州市吴江区虚构大道9号", "site": "苏州市吴江区虚构大道9号虚构广场",
        "contract_type": "买卖合同", "amount_label": "买卖", "sign_date": "2024-04-10", "qty": "2", "qty_actual": "2",
        "total": "500,000.00", "paid": "400,000.00", "unpaid": "100,000.00",
        "gov_accept": "2024-12-02", "last_pay_date": "2025-01-15", "reason": "电梯已验收交付，买方逾期未付到期货款。",
    })
    _text_pdf(os.path.join(ROOT, case, "materials", "苏州虚构广场项目电梯买卖合同.pdf"), [
        """电梯买卖合同

合同编号：AH9000002
买方（甲方）：苏州虚构商业管理有限公司
卖方（乙方）：日立电梯（中国）有限公司

第一条 标的
乙方向甲方供应电梯2台，用于苏州虚构广场项目，交货地点：苏州市吴江区虚构大道9号。

第二条 合同价款
本合同设备总价为人民币500,000元（大写：伍拾万元整）。

第三条 付款方式
3.1 合同签订后10日内，甲方支付合同价款的20%。
3.2 设备到达工地后15日内，甲方支付至合同价款的80%。
3.3 电梯经政府验收合格后30日内，甲方支付至合同价款的100%。""",
        """第四条 违约责任
4.1 乙方逾期交货的，按迟交部分货款的万分之三按日向甲方支付违约金。

第五条 争议解决
5.1 本合同争议的解决，按双方签订的《电梯战略合作协议》约定执行。

第六条 其他
本合同自双方盖章之日起生效。
（以下无正文）

买方（盖章）：苏州虚构商业管理有限公司
卖方（盖章）：日立电梯（中国）有限公司
签订日期：2024年4月12日""",
    ])
    _text_pdf(os.path.join(ROOT, case, "materials", "虚构商业集团电梯战略合作协议.pdf"), [
        """电梯战略合作协议

甲方：虚构商业集团有限公司（含其下属项目公司）
乙方：日立电梯（中国）有限公司

第一条 合作范围
甲方及其下属项目公司在合作期内采购电梯，按本协议约定的条件与乙方签订具体合同。

第十条 争议解决
10.1 因本协议及各项目具体合同引起的争议，提交上海国际经济贸易仲裁委员会，按其仲裁规则在上海仲裁。仲裁裁决是终局的。

第十一条 其他
本协议有效期三年，自双方盖章之日起生效。""",
    ])
    for i, code in enumerate(["31103205202400000021", "31103205202400000022"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-91{i:02d}", "user": "苏州虚构商业管理有限公司",
            "installer": "虚构电梯工程有限公司", "device_code": code,
            "inspect_date": "2024年11月25日", "approve_date": "2024年12月2日",
            "site": "苏州市吴江区虚构大道9号虚构广场", "model": "HGP",
        })


def case_install_vge() -> None:
    """合同 4 台（含 1 台 VGE 家用电梯）、验收报告 3 份；争议在原告所在地法院。"""
    case = "虚构安装案C"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000003无锡虚构房地产开发有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000103", "title": "AH9000003无锡虚构房地产开发有限公司虚构苑项目",
        "contract_no": "AH9000003", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "无锡虚构房地产开发有限公司", "contact": "虚构丁", "contact_phone": "13800000003",
        "buyer_address": "无锡市梁溪区虚构街3号", "site": "无锡市梁溪区虚构街3号虚构苑",
        "contract_type": "安装合同", "amount_label": "安装", "sign_date": "2024-02-20", "qty": "4", "qty_actual": "4",
        "total": "160,000.00", "paid": "120,000.00", "unpaid": "40,000.00",
        "gov_accept": "2024-09-26", "last_pay_date": "2024-10-30", "reason": "电梯已验收合格，买方拖欠安装款。",
    })
    # 设备清单是带框线的表格，合同按扫描件处理（真实案卷里含 VGE 的合同都是扫描件）
    _scanned_pdf(os.path.join(ROOT, case, "materials", "无锡虚构苑项目电梯安装合同.pdf"), [
        """电梯安装合同

合同编号：AH9000003
甲方：无锡虚构房地产开发有限公司
乙方：日立电梯（中国）有限公司梧桐分公司

第一条 工程概况
工程地点：无锡市梁溪区虚构街3号。乙方负责安装电梯共4台，明细见设备清单。

第二条 合同价款
本合同安装总价为人民币160,000元（大写：壹拾陆万元整）。""",
        ("""第三条 付款方式
3.1 电梯进场后10日内，甲方支付合同价款的30%。
3.2 电梯经政府验收合格后30日内，甲方支付至合同价款的100%。

附件：设备清单""", [["序号", "型号", "层站", "台数"], ["1", "HGP", "18/18/18", "3"],
                    ["2", "VGE-2", "3/3/3", "1"], ["合计", "", "", "4"]]),
        """第四条 违约责任
甲方逾期付款的，应按应付未付款项每日万分之三向乙方支付逾期付款违约金。

第五条 争议解决
因本合同发生的争议，双方协商不成的，向乙方所在地人民法院提起诉讼。

（以下无正文）
甲方（盖章）：无锡虚构房地产开发有限公司
乙方（盖章）：日立电梯（中国）有限公司梧桐分公司
签订日期：2024年2月22日""",
    ])
    for i, code in enumerate(["31103202202400000031", "31103202202400000032", "31103202202400000033"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-92{i:02d}", "user": "无锡虚构房地产开发有限公司",
            "installer": "日立电梯（中国）有限公司梧桐分公司", "device_code": code,
            "inspect_date": "2024年9月20日", "approve_date": "2024年9月26日",
            "site": "无锡市梁溪区虚构街3号虚构苑2栋", "model": "HGP",
        })


def case_lump_sum_penalty() -> None:
    """一次性违约金（合同总价的 5%）、合同签订地管辖（县级市法院）、无质保金。"""
    case = "虚构安装案D"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000004昆山虚构实业有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000104", "title": "AH9000004昆山虚构实业有限公司虚构厂房项目",
        "contract_no": "AH9000004", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "昆山虚构实业有限公司", "contact": "虚构戊", "contact_phone": "13800000004",
        "buyer_address": "昆山市虚构镇虚构路12号", "site": "昆山市虚构镇虚构路12号",
        "contract_type": "安装合同", "amount_label": "安装", "sign_date": "2024-05-08", "qty": "2", "qty_actual": "2",
        "total": "80,000.00", "paid": "40,000.00", "unpaid": "40,000.00",
        "gov_accept": "2024-10-15", "last_pay_date": "2024-08-01", "reason": "电梯已验收，买方未支付验收款。",
    })
    _text_pdf(os.path.join(ROOT, case, "materials", "昆山虚构厂房项目电梯安装合同.pdf"), [
        """电梯安装合同

合同编号：AH9000004
甲方：昆山虚构实业有限公司
乙方：日立电梯（中国）有限公司梧桐分公司
合同签订地：昆山市虚构镇

第一条 工程概况
工程地点：昆山市虚构镇虚构路12号。乙方负责安装货梯2台。

第二条 合同价款
本合同安装总价为人民币80,000元（大写：捌万元整）。

第三条 付款方式
3.1 电梯进场后10日内，甲方支付合同价款的50%。
3.2 电梯经政府验收合格后15日内，甲方支付至合同价款的100%。

第四条 违约责任
甲方未按约定付款的，应向乙方支付合同总价5%的违约金。

第五条 争议解决
因本合同发生的争议，双方协商不成的，向合同签订地人民法院提起诉讼。

（以下无正文）
甲方（盖章）：昆山虚构实业有限公司
乙方（盖章）：日立电梯（中国）有限公司梧桐分公司
签订日期：2024年5月10日""",
    ])
    for i, code in enumerate(["31103203202400000041", "31103203202400000042"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-93{i:02d}", "user": "昆山虚构实业有限公司",
            "installer": "日立电梯（中国）有限公司梧桐分公司", "device_code": code,
            "inspect_date": "2024年10月9日", "approve_date": "2024年10月15日",
            "site": "昆山市虚构镇虚构路12号", "model": "HGE",
        })


def case_unread_page() -> None:
    """扫描合同 10 页，条款与签章页在前 5 页、质保期只写在第 9 页附件里：按需 OCR 找齐条款就停，
    第 9 页没读到，质保期缺失 → 错误归因「可能未读到」（#108）。付款写「合同总价的X%」：
    按需 OCR 靠这种写法认出付款条款正文，写成「合同价款的X%」认不出、会一直读到最后一页。"""
    case = "虚构未读页案F"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000006常州虚构置业有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000106", "title": "AH9000006常州虚构置业有限公司虚构湾项目",
        "contract_no": "AH9000006", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "常州虚构置业有限公司", "contact": "虚构己", "contact_phone": "13800000006",
        "buyer_address": "常州市武进区虚构路6号", "site": "常州市武进区虚构路6号虚构湾",
        "contract_type": "安装合同", "amount_label": "安装", "sign_date": "2024-06-01", "qty": "2", "qty_actual": "2",
        "total": "120,000.00", "paid": "60,000.00", "unpaid": "60,000.00",
        "gov_accept": "2024-12-10", "last_pay_date": "2024-09-01", "reason": "电梯已验收，买方未付验收款。",
    })
    appendix = [f"附件{n}：施工组织说明（第{n}部分）\n本附件仅供施工参考，不构成付款条件。" for n in range(1, 4)]
    _scanned_pdf(os.path.join(ROOT, case, "materials", "常州虚构湾项目电梯安装合同.pdf"), [
        """电梯安装合同

合同编号：AH9000006
甲方：常州虚构置业有限公司
乙方：日立电梯（中国）有限公司梧桐分公司""",
        """第一条 工程概况
工程地点：常州市武进区虚构路6号。乙方负责安装电梯共2台。

第二条 合同价款
本合同安装总价为人民币120,000元（大写：壹拾贰万元整）。""",
        """第三条 付款方式
3.1 电梯进场后10日内，甲方支付合同总价的50%。
3.2 电梯经政府验收合格后30日内，甲方支付至合同总价的100%。""",
        """第四条 违约责任
甲方逾期付款的，每逾期一日按应付未付款的万分之五向乙方支付违约金。

第五条 争议解决
因本合同发生的争议，双方协商不成的，向工程所在地人民法院提起诉讼。""",
        """第六条 其他
本合同自双方盖章之日起生效，附件为本合同组成部分。
（以下无正文）
甲方（盖章）：常州虚构置业有限公司
乙方（盖章）：日立电梯（中国）有限公司梧桐分公司
签订日期：2024年6月3日""",
        *appendix,
        """附件4：质量保修
质保期：自电梯验收合格之日起24个月。""",
        """附件5：联系人名单
甲方现场联系人、乙方项目经理名单另行书面通知。""",
    ])
    for i, code in enumerate(["31103204202400000061", "31103204202400000062"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-96{i:02d}", "user": "常州虚构置业有限公司",
            "installer": "日立电梯（中国）有限公司梧桐分公司", "device_code": code,
            "inspect_date": "2024年12月3日", "approve_date": "2024年12月10日",
            "site": "常州市武进区虚构路6号虚构湾", "model": "HGP",
        })


def case_mixed_breach_clause() -> None:
    """同一条违约责任里既有乙方未按期交货的违约金、又有甲方逾期付款的利息（#124）→ 逾期付款利息。"""
    case = "虚构买卖案G"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000007苏州虚构置业有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000107", "title": "AH9000007苏州虚构置业有限公司虚构花园项目",
        "contract_no": "AH9000007", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "苏州虚构置业有限公司", "contact": "虚构庚", "contact_phone": "13800000007",
        "buyer_address": "苏州市吴江区虚构路7号", "site": "苏州市吴江区虚构路7号虚构花园",
        "contract_type": "买卖合同", "amount_label": "买卖", "sign_date": "2024-05-08", "qty": "2", "qty_actual": "2",
        "total": "600,000.00", "paid": "480,000.00", "unpaid": "120,000.00",
        "gov_accept": "2024-12-10", "last_pay_date": "2025-01-20", "reason": "电梯已验收交付，买方逾期未付到期货款。",
    })
    _text_pdf(os.path.join(ROOT, case, "materials", "苏州虚构花园项目电梯买卖合同.pdf"), [
        """电梯买卖合同

合同编号：AH9000007
买方（甲方）：苏州虚构置业有限公司
卖方（乙方）：日立电梯（中国）有限公司

第一条 标的
乙方向甲方供应电梯2台，用于苏州虚构花园项目，交货地点：苏州市吴江区虚构路7号。

第二条 合同价款
本合同设备总价为人民币600,000元（大写：陆拾万元整）。

第三条 付款方式
3.1 合同签订后10日内，甲方支付合同总价的20%。
3.2 设备到达工地后15日内，甲方支付至合同总价的80%。
3.3 电梯经政府验收合格后30日内，甲方支付至合同总价的100%。""",
        """第四条 违约责任
4.1 乙方未按期交货的，每逾期一日按迟交部分货款的万分之五向甲方支付违约金；甲方未按期付款的，应就逾期未付款项向乙方支付逾期付款利息。

第五条 争议解决
5.1 因本合同发生的争议，双方协商解决；协商不成的，向工程所在地人民法院提起诉讼。

第六条 其他
本合同自双方盖章之日起生效。
（以下无正文）

买方（盖章）：苏州虚构置业有限公司
卖方（盖章）：日立电梯（中国）有限公司
签订日期：2024年5月10日""",
    ])
    for i, code in enumerate(["31103205202400000071", "31103205202400000072"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-97{i:02d}", "user": "苏州虚构置业有限公司",
            "installer": "虚构电梯工程有限公司", "device_code": code,
            "inspect_date": "2024年12月3日", "approve_date": "2024年12月10日",
            "site": "苏州市吴江区虚构路7号虚构花园", "model": "HGP",
        })


def case_period_payment() -> None:
    """付款条款写成「第一期：……。第二期：……」、各期之间用句号（#127）→ 欠款付至 95%，引用第4.2条第二期原文。"""
    case = "虚构安装案H"
    os.makedirs(os.path.join(ROOT, case, "materials"), exist_ok=True)
    _approval(case, "AH9000008徐州虚构置业有限公司诉讼审批表.pdf", {
        "serial": "SS2099-000108", "title": "AH9000008徐州虚构置业有限公司虚构苑项目",
        "contract_no": "AH9000008", "branch_path": "集团/营销网络/梧桐分公司",
        "buyer": "徐州虚构置业有限公司", "contact": "虚构辛", "contact_phone": "13800000008",
        "buyer_address": "徐州市鼓楼区虚构路8号", "site": "徐州市鼓楼区虚构路8号虚构苑",
        "contract_type": "安装合同", "amount_label": "安装", "sign_date": "2024-03-06", "qty": "2", "qty_actual": "2",
        "total": "200,000.00", "paid": "80,000.00", "unpaid": "110,000.00",
        "gov_accept": "2024-09-20", "last_pay_date": "2024-04-01", "reason": "电梯已验收并移交，买方未支付验收款。",
    })
    _text_pdf(os.path.join(ROOT, case, "materials", "徐州虚构苑项目电梯安装合同.pdf"), [
        """电梯安装合同

合同编号：AH9000008
甲方：徐州虚构置业有限公司
乙方：日立电梯（中国）有限公司梧桐分公司

第一条 工程概况
工程地点：徐州市鼓楼区虚构路8号虚构苑。乙方负责安装客梯2台。

第二条 合同价款
本合同安装总价为人民币200,000元（大写：贰拾万元整）。

第三条 工期
乙方在电梯到场后60日内完成安装。

第四条 付款条件
4.1 甲方须采用电汇方式向乙方付款。
4.2 甲方分三期向乙方支付安装费用，具体如下：第一期：甲方在安装前7日向乙方支付需要安装楼号电梯总价的40%的安装款，作为动工款。第二期：在经技术监督部门验收合格后15日内，乙方向甲方移交电梯钥匙、监督检验报告等全部资料后，甲方应向乙方支付已安装完楼号电梯总价的55%安装款作为验收款。第三期：余款5%为质保金，质保期（自验收合格起24个月）满后无质保问题后30日内无息付清。""",
        """第五条 违约责任
甲方逾期付款的，应向乙方支付逾期付款利息。

第六条 争议解决
因本合同发生的争议，双方协商不成的，向工程所在地人民法院提起诉讼。

（以下无正文）
甲方（盖章）：徐州虚构置业有限公司
乙方（盖章）：日立电梯（中国）有限公司梧桐分公司
签订日期：2024年3月8日""",
    ])
    for i, code in enumerate(["31103203202400000081", "31103203202400000082"], start=1):
        _report(case, f"电梯监督检验报告{i}.pdf", {
            "report_no": f"XG-DTJ-2024-98{i:02d}", "user": "徐州虚构置业有限公司",
            "installer": "日立电梯（中国）有限公司梧桐分公司", "device_code": code,
            "inspect_date": "2024年9月13日", "approve_date": "2024年9月20日",
            "site": "徐州市鼓楼区虚构路8号虚构苑", "model": "HGP",
        })


if __name__ == "__main__":
    case_install_scanned()
    case_sales_arbitration()
    case_install_vge()
    case_lump_sum_penalty()
    case_unread_page()
    case_mixed_breach_clause()
    case_period_payment()
    print("已生成：", ", ".join(sorted(n for n in os.listdir(ROOT) if not n.startswith(("_", ".")) and os.path.isdir(os.path.join(ROOT, n)))))
