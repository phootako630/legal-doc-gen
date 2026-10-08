# 派生字段：按律师确认的填写规则，由已抽取字段确定性推出起诉状里的若干取值与措辞。
#
# 规则来源：律师诉状模板的红字注释 + 两份《起诉状规则确认单》的答复（题号见各函数，「补」=补充单）。
#   - 原告：买卖合同 = 总公司全称；安装合同 = 总公司全称 + 审批表「合同分公司」中的第一个分公司；
#     再与合同盖章页乙方核对，不一致以合同乙方为准（第 6 题、补 1）；总公司写「法定代表人」（补 7）
#   - 原告电话固定；信用代码 / 负责人 / 住址取分公司信息表（第 7 题）
#   - 买卖合同措辞：「安装」→「供货」，「安装价格」→「产品价格」（第 18 题）
#   - 台数：约定台数取合同，验收台数取验收报告；合同含 VGE 家用电梯时核对差额（第 3 题、补 2）
#   - 逾期利息：合同有甲方逾期付款利率则用合同约定，否则用 LPR 常规话术（第 14 题、补 3）
#   - 管辖：按争议条款约定的地点写管辖句与致送法院；约定仲裁则整篇改为仲裁申请书（第 13 题、补 6）
#   - 「已全部移交物业并办理结算」：合同付款条件有「结算」才写「并办理结算」（补 5）
#   - 付款比例：合同有质保金时按律师选择写「支付至 X% 合同款」（第 9 题、补 4）
#   - 第三轮确认单：违约条款写「违约金」则诉状写违约金并引用条款（第 4 题）；已付 / 欠款默认写
#     占合同款比例，二者之和不等于总价时写算式（第 6 题）；交货地管辖（第 9 题）；独立工程公司
#     做原告写自己的全称和「法定代表人」（第 12 题）；只有总公司写职务（第 13 题）；
#     管辖依据写法律全称（第 16 题）
# 律师改过的值（src 以「律师」开头）一律保留，不覆盖：AI 只提议，律师拍板。
from __future__ import annotations

import re
from collections.abc import Callable

from app.config import PLAINTIFF_HQ_NAME, PLAINTIFF_HQ_REP_TITLE, PLAINTIFF_PHONE
from app.services.branch_registry import lookup_plaintiff, resolve_plaintiff_name
from app.services.branch_table import normalize_name
from app.services.validators import parse_amount, parse_qty

# 逾期付款利息的常规话术（诉状中"按照……计至实际付清之日止"）
# 补充确认单第 3 题：以「全国银行间同业拆借中心公布的一年期贷款市场报价利率」为准
LPR_INTEREST_BASIS = "全国银行间同业拆借中心公布的一年期贷款市场报价利率"
# 未约定管辖地点时的依据（补充确认单第 6 题：第24条，不引条文原文；第三轮第 16 题：写法律全称）
_GENERAL_JURISDICTION_BASIS = "依据《中华人民共和国民事诉讼法》第二十四条"
_MISSING = "【待补充】"

_CJK = "\\u4e00-\\u9fa5"  # 常用汉字区间（正则转义形式）
# 地址前常见的方位词（「位于南京市…」），不去掉会被误当成市名的一部分
_LEADING_WORDS_RE = re.compile(r"^(?:项目|工程)?(?:位于|坐落于|地处|地址为|地址：|在)")
_PROVINCE_RE = re.compile(f"^[{_CJK}]{{2,7}}?(?:省|自治区)")
# 市 + 区/县/县级市/旗；从串首匹配，匹配不上宁可不推定（不猜）
_CITY_DISTRICT_RE = re.compile(
    f"^([{_CJK}]{{2,6}}?市)([{_CJK}]{{1,6}}?(?:新区|区|县|市|旗))"
)
# 争议条款里点名的具体法院（不是「所在地 / 当地」法院）
_NAMED_COURT_RE = re.compile(r"(?:[市区县]|中级)人民法院")
_PLAINTIFF_SIDE = "原告|乙方|承包方|承包人|卖方|供方|出卖人"
_DEFENDANT_SIDE = "被告|甲方|发包方|发包人|买方|需方"
_PLAINTIFF_SEAT_RE = re.compile(f"(?:{_PLAINTIFF_SIDE})(?:所在地|住所地)")
_DEFENDANT_SEAT_RE = re.compile(f"(?:{_DEFENDANT_SIDE})(?:所在地|住所地)")
_SITE_RE = re.compile(r"工程所在地|项目所在地|合同履行地|履行地|交货地")
_DELIVERY_RE = re.compile(r"交货地")
_SIGN_PLACE_RE = re.compile(r"合同签订地|签订地")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# 争议条款里的仲裁机构名（LLM 未抽出时的兜底）；去掉前面的动词
_ARBITRATION_BODY_RE = re.compile(f"([{_CJK}]{{2,20}}?仲裁委员会)")
_LEADING_VERBS_RE = re.compile(r"^(?:提交|提请|交由|申请|向|由|至|到|报)+")


def _node(fields: dict, key: str) -> dict:
    node = fields.get(key)
    return node if isinstance(node, dict) else {"value": node}


def _str_value(fields: dict, key: str) -> str | None:
    val = _node(fields, key).get("value")
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def derive_court_district(address: str | None) -> str | None:
    """
    由地址推定法院辖区（「市+区/县」），如
    「江苏省南京市雨花台区某某二期项目」→「南京市雨花台区」。推不出返回 None。
    """
    if not address:
        return None
    s = re.sub(r"\s+", "", address)
    s = _LEADING_WORDS_RE.sub("", s, count=1)
    s = _PROVINCE_RE.sub("", s, count=1)
    m = _CITY_DISTRICT_RE.match(s)
    if not m:
        return None
    return m.group(1) + m.group(2)


# ── 合同类型与原告 ────────────────────────────────────────────────────────────
def contract_kind(fields: dict) -> str:
    """审批表「合同类型」→ '买卖' / '安装'。识别不出按安装合同处理（本系统的主场景）。"""
    raw = _str_value(fields, "contract_type") or ""
    if any(w in raw for w in ("买卖", "设备", "供货", "采购")):
        return "买卖"
    return "安装"


def _is_branch(name: str) -> bool:
    return name.endswith("分公司")


def derive_plaintiff_name(kind: str, branch_raw: str | None) -> tuple[str, bool] | None:
    """
    原告全称。返回 (名称, 是否需核实)；推不出返回 None。
    安装合同取「合同分公司」路径中第一个以「分公司」结尾的层级（如「集团/营销网络/江苏分公司」
    → 江苏分公司），前面拼总公司全称；路径里有多个分公司时仍取第一个，但标待核实。
    路径里是独立的工程 / 营销公司（以「有限公司」结尾、不是总公司本身，如「无锡日立电梯工程
    有限公司」）时，原告就是该公司，不拼总公司（第三轮确认单第 12 题）；写的是简称时按原告信息表
    补成全称。
    """
    if kind == "买卖":
        return PLAINTIFF_HQ_NAME, False
    if not branch_raw:
        return None
    hq = normalize_name(PLAINTIFF_HQ_NAME)
    segments = [normalize_name(s) for s in re.split(r"[/／\\\\>]", branch_raw)]
    candidates = [
        s for s in segments if _is_branch(s) or (s.endswith("有限公司") and s != hq)
    ]
    if not candidates:
        return None
    name = candidates[0]
    if _is_branch(name):
        full = name if name.startswith(hq) else hq + name
        return full, sum(_is_branch(s) for s in segments) > 1
    # 独立公司：信息表里有就用表中全称；查不到照抄并标待核实（可能是简称）
    resolved = resolve_plaintiff_name(name)
    return (resolved, False) if resolved else (name, True)


def _compact(text: str | None) -> str | None:
    return re.sub(r"\s+", "", text) if text else None


def plaintiff_name_field(fields: dict) -> dict | None:
    """
    原告全称：先按审批表规则推出，再与合同盖章页乙方（卖方 / 安装方）核对；
    不一致以合同乙方为准并标待核实（补充确认单第 1 题）。
    """
    kind = contract_kind(fields)
    branch_raw = _str_value(fields, "plaintiff_branch_raw")
    derived = derive_plaintiff_name(kind, branch_raw)
    party_b = _compact(_str_value(fields, "contract_party_b"))
    if derived is None:
        if not party_b:
            return None
        # 审批表「合同分公司」未填（独立工程公司签约时常见）：以合同盖章页乙方为准（第三轮第 12 题）
        return {
            "value": party_b,
            "src": "按律师规则：审批表「合同分公司」未识别，取自合同盖章页乙方 / 安装方名称",
            "channel": "text",
        }
    name, uncertain = derived
    if kind == "买卖":
        src = "按律师规则：买卖合同原告为总公司"
    elif _is_branch(name):
        src = f"按律师规则：总公司全称 + 审批表「合同分公司」（{branch_raw}）中的分公司"
        if uncertain:
            src += "；路径中有多个分公司，待核实"
    else:
        src = f"按律师规则：审批表「合同分公司」（{branch_raw}）为独立公司，写该公司全称"
        if uncertain:
            src += "；原告信息表中未找到该公司，待核实"
    if party_b and party_b != name:
        return {
            "value": party_b,
            "src": f"按律师规则：以合同盖章页乙方名称为准；与审批表推出的「{name}」不一致，待核实",
            "channel": "text",
        }
    if party_b:
        src += "；与合同盖章页乙方一致"
    return {"value": name, "src": src, "channel": "text"}


def plaintiff_rep_label_field(fields: dict) -> dict:
    """
    原告第三行标签：分公司写「负责人」，总公司和独立公司写「法定代表人」
    （补充确认单第 7 题、第三轮第 12 题）。原告名称未定时按合同类型判断。
    """
    name = _str_value(fields, "plaintiff_name_final")
    if name:
        is_branch = _is_branch(normalize_name(name))
        src = "原告名称为分公司" if is_branch else "原告名称为公司（非分公司）"
    else:
        is_branch = contract_kind(fields) != "买卖"
        src = "合同类型"
    return {"value": "负责人" if is_branch else "法定代表人", "src": src}


def plaintiff_phone_field(_fields: dict) -> dict:
    return {
        "value": PLAINTIFF_PHONE,
        "src": "按律师规则：原告电话统一",
        "channel": "text",
    }


def _registry_field(attr: str) -> Callable[[dict], dict | None]:
    """
    从原告信息表取原告的某一项，来源注明表的版本日期。表里没有则返回 None：
    此前没有值就保持缺失；此前是从表里推出的旧原告的值（律师把原告从甲改成乙），
    由 apply_derived_fields 清除，不会生成「乙名称 + 甲信息」。
    """

    def derive(fields: dict) -> dict | None:
        info = lookup_plaintiff(_str_value(fields, "plaintiff_name_final"))
        value = getattr(info, attr, None) if info else None
        if not value:
            return None
        src = f"《原告信息表》{info.version} 版" if info.version else "《原告信息表》"
        return {"value": value, "src": src, "channel": "text"}

    return derive


# 信息表「法定代表人 / 负责人」一栏里姓名后已写的职务：逗号 / 顿号后的任何文字，
# 或空白后的常见职务名（只按空白切会把「網谷 憲晴」这类带空格的姓名误当成职务）
_TITLE_AFTER_COMMA_RE = re.compile(r"^(.+?)\s*[，,、]\s*(\S.*)$")
_TITLE_AFTER_SPACE_RE = re.compile(
    r"^(.+?)\s+(\S*(?:董事长|董事|总经理|经理|总裁|负责人|厂长|主任|主席|行长))$"
)


def _existing_title(person: str) -> str | None:
    m = _TITLE_AFTER_COMMA_RE.match(person) or _TITLE_AFTER_SPACE_RE.match(person)
    return m.group(2).strip() if m else None


def plaintiff_rep_field(fields: dict) -> dict | None:
    """
    原告法定代表人 / 负责人：取原告信息表；总公司只有姓名时加默认职务（「张三，董事长」），
    分公司和独立公司不写职务（第三轮确认单第 13 题）。
    信息表已写了职务的保留原写法、不再追加；与默认职务不同的提醒律师核对（#31）。
    """
    node = _registry_field("person_in_charge")(fields)
    if node is None:
        return None
    name = normalize_name(_str_value(fields, "plaintiff_name_final") or "")
    if name != normalize_name(PLAINTIFF_HQ_NAME) or not PLAINTIFF_HQ_REP_TITLE:
        return node
    person = str(node["value"]).strip()
    title = _existing_title(person)
    if title is None:
        node["value"] = f"{person}，{PLAINTIFF_HQ_REP_TITLE}"
        node["src"] += f"；按律师规则：总公司加职务「{PLAINTIFF_HQ_REP_TITLE}」"
    elif PLAINTIFF_HQ_REP_TITLE not in title:
        node["src"] += (
            f"；信息表已写职务「{title}」，与默认「{PLAINTIFF_HQ_REP_TITLE}」不同，"
            "保留原写法，待核实"
        )
    return node


def contract_action_field(fields: dict) -> dict:
    """「约定原告负责__N台电梯」：安装 / 供货。"""
    return {
        "value": "供货" if contract_kind(fields) == "买卖" else "安装",
        "src": "合同类型",
    }


def price_term_field(fields: dict) -> dict:
    """「双方对__、付款方式、违约责任等条款进行了约定」：安装价格 / 产品价格。"""
    value = "产品价格" if contract_kind(fields) == "买卖" else "安装价格"
    return {"value": value, "src": "合同类型"}


# ── 台数 ──────────────────────────────────────────────────────────────────────
def elevator_qty_contract_field(fields: dict) -> dict | None:
    """「约定原告负责安装 N 台」：取合同约定台数；合同没识别到时退回审批表签约台数（补 2）。"""
    contract = parse_qty(_str_value(fields, "elevator_qty_by_contract"))
    if contract is not None:
        # 沿用合同台数的出处（页码、命中原文、通道）：扫描合同 OCR 出来的台数必须保留
        # 「OCR识别，请核实」，否则审核页显示正常、起诉状也不标待核实
        base = fields.get("elevator_qty_by_contract")
        node: dict = {"value": contract, "src": "合同约定台数", "channel": "text"}
        if isinstance(base, dict):
            base_src = str(base.get("src") or "")
            if base_src:
                node["src"] = f"合同约定台数（{base_src}）"
            for k in ("page", "anchor", "channel", "confidence"):
                if base.get(k) is not None:
                    node[k] = base[k]
            if node["channel"] == "ocr" and "OCR" not in node["src"]:
                node["src"] += "（OCR识别，请核实）"
        return node
    approval = parse_qty(_str_value(fields, "elevator_qty_by_approval"))
    if approval is not None:
        return {"value": approval, "src": "合同台数未识别，暂取审批表签约台数，待核实"}
    return None


def elevator_qty_field(fields: dict) -> dict | None:
    """
    「N 台电梯均于……验收合格」：取验收报告台数；没有则不派生（保留抽取值）。
    合同台数与报告不一致时：差额正好是 VGE 家用电梯（无需验收报告）→ 继续并提醒；
    其余情况由 qty_check 断点问律师（补充确认单第 2 题）。
    """
    acceptance = parse_qty(_str_value(fields, "elevator_qty_by_acceptance"))
    if acceptance is None:
        return None
    approval = parse_qty(_str_value(fields, "elevator_qty_by_approval"))
    contract = parse_qty(_str_value(fields, "elevator_qty_by_contract"))
    vge = parse_qty(_str_value(fields, "elevator_qty_vge")) or 0
    src = "按验收报告台数"
    if contract is not None and contract != acceptance:
        if vge and acceptance == contract - vge:
            src += (
                f"；合同 {contract} 台中含 VGE 家用电梯 {vge} 台（无需验收报告），"
                "存在家用电梯，待核实"
            )
        else:
            src += f"；与合同 {contract} 台不一致，待核实"
    elif approval is not None and approval == contract and approval != acceptance:
        src += (
            f"；审批表与合同均为 {approval} 台，与验收报告 {acceptance} 台不同，待核实"
        )
    elif approval is not None and approval != acceptance:
        src += f"（审批表 {approval} 台）"
    return {"value": acceptance, "src": src, "channel": "text"}


def handover_text_field(fields: dict) -> dict:
    """
    「案涉电梯___，被告应按合同约定支付至…」：至少写「已全部移交物业」；
    合同付款条件提到「结算」才加「并办理结算」（补充确认单第 5 题）。
    """
    clause = _str_value(fields, "payment_clause_text") or ""
    if not clause:
        return {
            "value": "已全部移交物业",
            "src": "未识别到付款条款，是否写「并办理结算」待核实",
        }
    if "结算" in clause:
        return {"value": "已全部移交物业并办理结算", "src": "合同付款条件含「结算」"}
    return {"value": "已全部移交物业", "src": "合同付款条件未约定结算"}


# ── 逾期利息 / 违约金 ─────────────────────────────────────────────────────────
def _breach_is_penalty(fields: dict) -> bool:
    """合同的甲方逾期付款条款写的是「违约金」（而非利息）。"""
    text = (_str_value(fields, "breach_clause_text") or "") + (
        _str_value(fields, "breach_interest_rate_text") or ""
    )
    return "违约金" in text


def interest_term_field(fields: dict) -> dict:
    """
    诉请第 2 项与事实理由的措辞：合同违约条款约定的是「违约金」就写违约金，
    否则写「逾期付款利息」（第三轮确认单第 4 题，安装 / 买卖合同一样）。
    """
    if _breach_is_penalty(fields):
        return {"value": "违约金", "src": "合同甲方逾期付款条款约定为违约金"}
    return {"value": "逾期付款利息", "src": "合同未约定甲方逾期付款违约金"}


def breach_clause_sentence_field(fields: dict) -> dict:
    """
    约定违约金时在违约段落前引用违约条款：「依据合同第11.4条约定，……。」（第三轮第 4 题）。
    没有违约金约定时为空串：模板中这一句整句不出现，不算待补充。
    """
    if not _breach_is_penalty(fields):
        return {"value": "", "src": "合同未约定违约金，不引用违约条款"}
    location = _str_value(fields, "breach_interest_clause_location") or _MISSING
    text = (_str_value(fields, "breach_clause_text") or _MISSING).rstrip("。；;，, ")
    return {
        "value": f"依据合同{location}约定，{text}。",
        "src": "按律师规则：合同约定违约金，引用违约条款原文，待核实",
        "channel": "text",
    }


# 能按时间持续计算的标准（「日万分之五」「年利率6%」「每月1%」）
_PERIODIC_RATE_RE = re.compile(r"[日天月年]")


def interest_basis_field(fields: dict) -> dict:
    """
    合同有甲方逾期付款的利率约定就用约定（待核实措辞），否则 LPR 常规话术。
    约定的是违约金、却没有按日 / 按月 / 按年的计算标准（固定金额、一次性比例或没识别到）时，
    不套用 LPR 持续计付：计算标准留【待补充】交律师（#30，写法待律师确认）。
    """
    breach = _str_value(fields, "breach_interest_rate_text")
    if _breach_is_penalty(fields) and not (breach and _PERIODIC_RATE_RE.search(breach)):
        return {
            "value": None,
            "src": "合同约定违约金，但未识别到按日 / 按年等可持续计算的标准"
            "（可能是固定金额或一次性比例），计算方式请律师确认，不套用 LPR",
        }
    if breach:
        return {
            "value": breach,
            "src": "按律师规则：合同约定了甲方逾期付款利率，采用合同约定，待核实",
            "channel": "text",
        }
    return {
        "value": LPR_INTEREST_BASIS,
        "src": "常规话术（合同未约定甲方逾期付款利率）",
        "channel": "text",
    }


# ── 已付 / 欠款的写法 ────────────────────────────────────────────────────────
def _amount_parts(fields: dict) -> tuple[float | None, float | None, float | None]:
    return (
        parse_amount(_node(fields, "total_amount").get("value")),
        parse_amount(_node(fields, "paid_amount").get("value")),
        parse_amount(_node(fields, "unpaid_amount").get("value")),
    )


def _plain_number(num: float) -> str:
    """金额 / 比例的数字写法：到分，去掉多余尾零（256266.80 → 256266.8）。"""
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _ratio_mode(fields: dict) -> bool | None:
    """
    第三轮确认单第 6 题：已付 + 欠款 = 合同总价 → 写「占合同款的 X%」（True）；
    不等（如尚有未到期的质保金）→ 写算式「（到期金额-已付）」（False）。已付或欠款缺失 → None。
    """
    total, paid, unpaid = _amount_parts(fields)
    if paid is None or unpaid is None:
        return None
    return total is not None and total > 0 and abs(total - paid - unpaid) <= 0.01


def paid_note_field(fields: dict) -> dict:
    """「仅支付了¥X元（占合同款的80%）」括号部分；写算式时为空。"""
    total, paid, _ = _amount_parts(fields)
    if _ratio_mode(fields) and total and paid is not None:
        return {
            "value": f"（占合同款的{_plain_number(paid / total * 100)}%）",
            "src": "按律师规则：已付 + 欠款 = 合同总价，写占合同款比例",
        }
    return {"value": "", "src": "不写比例"}


def unpaid_note_field(fields: dict) -> dict:
    """「尚欠剩余合同款¥Y元（占合同款的20%）」或「（到期金额-已付）」算式。"""
    mode = _ratio_mode(fields)
    total, paid, unpaid = _amount_parts(fields)
    if mode is None or paid is None or unpaid is None:
        return {"value": "", "src": "已付或欠款金额缺失，不写比例 / 算式"}
    if mode and total:
        return {
            "value": f"（占合同款的{_plain_number(unpaid / total * 100)}%）",
            "src": "按律师规则：已付 + 欠款 = 合同总价，写占合同款比例",
        }
    return {
        "value": f"（{_plain_number(paid + unpaid)}-{_plain_number(paid)}）",
        "src": "按律师规则：已付 + 欠款 ≠ 合同总价，写算式（到期金额-已付款），待核实",
    }


# ── 管辖 ──────────────────────────────────────────────────────────────────────
def _jurisdiction(fields: dict) -> tuple[str, str | None, str]:
    """
    按争议条款判定管辖方式，返回 (方式, 法院辖区或法院名, 说明)。
    方式：arbitration / named / plaintiff / defendant / sign_place / site / delivery / general。
    """
    dispute = re.sub(r"\s+", "", _str_value(fields, "dispute_clause_text") or "")
    if "仲裁" in dispute:
        return "arbitration", None, "合同约定仲裁，改为仲裁申请书，待核实"
    if _NAMED_COURT_RE.search(dispute) and not (
        _PLAINTIFF_SEAT_RE.search(dispute) or _DEFENDANT_SEAT_RE.search(dispute)
    ):
        # 点名了具体法院：从文字里截出法院名不可靠，交律师填写（宁缺勿造）
        return "named", None, "争议条款约定了具体法院，请律师填写"
    if _PLAINTIFF_SEAT_RE.search(dispute):
        addr = _str_value(fields, "plaintiff_address")
        return (
            "plaintiff",
            derive_court_district(addr),
            "约定原告所在地法院，按原告住址推定，待核实",
        )
    if _DEFENDANT_SEAT_RE.search(dispute):
        addr = _str_value(fields, "defendant_address")
        return (
            "defendant",
            derive_court_district(addr),
            "约定被告所在地法院，按被告住址推定，待核实",
        )
    if _SIGN_PLACE_RE.search(dispute) and not _SITE_RE.search(dispute):
        return "sign_place", None, "约定合同签订地法院，请律师确定具体法院"
    if _SITE_RE.search(dispute) or not dispute:
        return _site_jurisdiction(fields, delivery=bool(_DELIVERY_RE.search(dispute)))
    addr = _str_value(fields, "defendant_address")
    return (
        "general",
        derive_court_district(addr),
        "未约定具体地点，按被告住所地推定，待核实",
    )


def _site_place(fields: dict, delivery: bool) -> tuple[str | None, str]:
    """
    工程所在地 / 交货地点的取值与名称。约定交货地点法院时只取合同约定的交货地点：
    没抽到就是没有，不拿工程地点充当交货地点（#29）。
    """
    if delivery:
        return _str_value(fields, "delivery_place"), "交货地点"
    return _str_value(fields, "project_site"), "工程所在地"


def _site_jurisdiction(fields: dict, delivery: bool) -> tuple[str, str | None, str]:
    """
    按工程所在地 / 交货地点推定法院辖区。地点写到区县的直接采纳；只写了项目名的，
    按验收报告的安装地点推断并提醒律师（第三轮确认单第 9 题）。
    """
    mode = "delivery" if delivery else "site"
    place, label = _site_place(fields, delivery)
    district = derive_court_district(place)
    if district:
        return mode, district, f"按{label}「{place}」推定，待核实管辖"
    install = _str_value(fields, "install_address")
    district = derive_court_district(install)
    if district:
        return (
            mode,
            district,
            f"{_place_desc(label, place)}，系统按验收报告安装地点「{install}」"
            "推断，待核实管辖",
        )
    return mode, None, f"{_place_desc(label, place)}，请律师确定管辖法院"


def _place_desc(label: str, place: str | None) -> str:
    return f"{label}「{place}」未写明区县" if place else f"合同{label}未识别"


def court_district_field(fields: dict) -> dict:
    mode, district, note = _jurisdiction(fields)
    if district is None:
        return {"value": None, "src": note}
    return {"value": district, "src": note, "channel": "text"}


def arbitration_institution(fields: dict) -> str | None:
    """仲裁机构全称：优先 LLM 抽取值，否则从争议条款截取（去掉前面的动词）。"""
    name = _compact(_str_value(fields, "arbitration_institution"))
    if name:
        return name
    dispute = _compact(_str_value(fields, "dispute_clause_text")) or ""
    m = _ARBITRATION_BODY_RE.search(dispute)
    return _LEADING_VERBS_RE.sub("", m.group(1)) if m else None


def document_kind_field(fields: dict) -> dict:
    """文书类型：争议条款约定仲裁 → 仲裁申请书，否则民事起诉状（补充确认单第 6 题）。"""
    mode, _, _ = _jurisdiction(fields)
    if mode == "arbitration":
        return {"value": "仲裁申请书", "src": "争议条款约定仲裁，待核实"}
    return {"value": "民事起诉状", "src": "争议条款约定诉讼"}


def jurisdiction_text_field(fields: dict) -> dict:
    """争议条款之后的管辖句（第 13 题、补 6 的各种写法 + 工程所在地的模板原句）。"""
    mode, _, note = _jurisdiction(fields)
    court = _str_value(fields, "court_district") or _MISSING
    if mode == "arbitration":
        body = arbitration_institution(fields) or _MISSING
        text = f"故申请人向{body}提请仲裁。"
    elif mode in ("site", "delivery"):
        place, label = _site_place(fields, mode == "delivery")
        text = (
            f"因{label}为{place or _MISSING}，属{court}法院辖区，"
            f"故原告向{court}人民法院提起诉讼。"
        )
    elif mode == "general":
        text = f"{_GENERAL_JURISDICTION_BASIS}，故原告向{court}人民法院提起诉讼。"
    else:
        text = f"故原告向{court}人民法院提起诉讼。"
    return {
        "value": text,
        "src": note + "（管辖句由系统按律师规则生成，待核实）",
        "channel": "text",
    }


def addressee_field(fields: dict) -> dict:
    """「此致」下一行：XX人民法院 / XX仲裁委员会。"""
    mode, _, note = _jurisdiction(fields)
    if mode == "arbitration":
        body = arbitration_institution(fields)
        return {"value": body or _MISSING, "src": "合同约定的仲裁机构，待核实"}
    court = _str_value(fields, "court_district")
    if court:
        return {"value": f"{court}人民法院", "src": note}
    return {"value": f"{_MISSING}人民法院", "src": note or "请律师填写受理法院"}


# ── 付款比例（质保金）────────────────────────────────────────────────────────
def parse_percent(raw: str | None) -> float | None:
    """「5%」→5；「无」「0」→0；识别不出返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("无", "没有", "不涉及", "0", "0%"):
        return 0.0
    m = _PERCENT_RE.search(s)
    return float(m.group(1)) if m else None


def retention_stages(fields: dict) -> list[float]:
    """
    质保金分期比例：从质保金条款原文里取百分比（如「满一年付2%，满二年付3%」→ [2, 3]）；
    原文取不到或与质保金总比例对不上时，按总比例一期处理（如 [5]）。无质保金返回 []。
    """
    total = parse_percent(_str_value(fields, "retention_ratio"))
    text = _str_value(fields, "retention_clause_text") or ""
    stages = [float(x) for x in _PERCENT_RE.findall(text)]
    stages = [x for x in stages if 0 < x < 100]
    if stages and (total is None or abs(sum(stages) - total) < 1e-6):
        return stages
    return [total] if total else []


def payable_ratio_options(fields: dict) -> list[str]:
    """律师可选的「支付至 X%」：全部质保金到期 100%，逐期扣除未到期部分（如 100% / 97% / 95%）。"""
    stages = retention_stages(fields)
    options = []
    for k in range(len(stages) + 1):
        options.append(f"{100 - sum(stages[k:]):g}%")
    return sorted(set(options), key=lambda x: -float(x.rstrip("%")))


def payable_ratio_field(fields: dict) -> dict:
    """「被告应按合同约定支付至__合同款」：无质保金 100%；有质保金由 retention 断点问律师。"""
    ratio = parse_percent(_str_value(fields, "retention_ratio"))
    if ratio == 0:
        return {"value": "100%", "src": "合同无质保金"}
    if ratio is None:
        return {"value": "100%", "src": "未识别到质保金约定，待核实"}
    return {"value": "100%", "src": f"合同质保金 {ratio:g}%，请确认支付比例，待核实"}


# ── 统一入口 ──────────────────────────────────────────────────────────────────
# (字段, 派生函数, 是否覆盖抽取值)。覆盖=True：规则比 LLM 抽取更可靠，派生得出就替换；
# 覆盖=False：只在缺失或上次也是派生值时填。顺序有依赖：原告名称在信息表查询之前，
# 管辖辖区在管辖句之前，台数在前。
_DERIVERS: list[tuple[str, Callable[[dict], dict | None], bool]] = [
    ("plaintiff_name_final", plaintiff_name_field, True),
    ("plaintiff_rep_label", plaintiff_rep_label_field, True),
    ("plaintiff_phone", plaintiff_phone_field, True),
    ("plaintiff_credit_code", _registry_field("credit_code"), True),
    ("plaintiff_person_in_charge", plaintiff_rep_field, True),
    ("plaintiff_address", _registry_field("address"), True),
    ("contract_action", contract_action_field, True),
    ("price_term", price_term_field, True),
    ("elevator_qty_contract", elevator_qty_contract_field, True),
    ("elevator_qty", elevator_qty_field, True),
    ("handover_text", handover_text_field, False),
    ("interest_term", interest_term_field, True),
    ("breach_clause_sentence", breach_clause_sentence_field, False),
    ("interest_rate_basis", interest_basis_field, False),
    ("paid_note", paid_note_field, True),
    ("unpaid_note", unpaid_note_field, True),
    ("document_kind", document_kind_field, False),
    ("court_district", court_district_field, False),
    ("jurisdiction_text", jurisdiction_text_field, False),
    ("addressee", addressee_field, False),
    ("payable_ratio", payable_ratio_field, False),
]


def _lawyer_edited(node: object) -> bool:
    """律师修改路径（审核页编辑「律师人工确认修改」/ 断点决定「律师确认修改」）改写过 src。"""
    return isinstance(node, dict) and str(node.get("src") or "").startswith("律师")


def _is_empty(node: object) -> bool:
    value = node.get("value") if isinstance(node, dict) else node
    return value is None or str(value).strip() == ""


def apply_derived_fields(fields: dict) -> dict:
    """就地补齐/刷新派生字段（律师修改过的值不覆盖），返回 fields 以便链式调用。"""
    for key, derive, override in _DERIVERS:
        node = fields.get(key)
        if _lawyer_edited(node):
            continue
        was_derived = isinstance(node, dict) and bool(node.get("derived"))
        if not (_is_empty(node) or override or was_derived):
            continue
        result = derive(fields)
        if result is not None:
            fields[key] = {**result, "derived": True}
        elif was_derived:
            # 之前由规则推出、现在依据没了（如律师改了上游字段）：清掉旧推定值，不留过期结论
            fields[key] = {
                "value": None,
                "src": "依据已变更（如原告已更换），原自动填写的值已清除，待补充",
                "derived": True,
            }
    return fields
