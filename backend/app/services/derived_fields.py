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

import calendar
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.config import PLAINTIFF_HQ_NAME, PLAINTIFF_HQ_REP_TITLE, PLAINTIFF_PHONE
from app.services.branch_registry import lookup_plaintiff, resolve_plaintiff_name
from app.services.branch_table import normalize_name
from app.services.payment_nodes import PaymentStage, match_cumulative, owed_stages, parse_payment_stages
from app.services.validators import parse_amount, parse_date, parse_qty

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
# 合同常把地级市的「市」字省掉（「苏州吴江区锦湖路以北……」）：两字城市名 + 市辖区。
# 只认以「区」结尾的市辖区，不认开发区 / 园区等功能区（它们未必设法院）；结果照常标待核实
# 只认「两字城市 + 两字区名 + 区」（「苏州吴江区」）：三四字城市（乌鲁木齐、呼和浩特、石家庄）
# 和三字区名分不清边界，宁可不推、交律师填（#43 复审：不能凭长度拆分拼造辖区）
_CITY_NO_SHI_RE = re.compile(f"^([{_CJK}]{{2}})([{_CJK}]{{2}}区)")
_FUNCTION_ZONE_RE = re.compile(r"(?:开发|园|高新|新|保税|工业)区$")
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
    由地址推定法院辖区，如「江苏省南京市雨花台区某某二期项目」→「南京市雨花台区」。
    县级市、县只写县级市 / 县：「江苏省苏州市昆山市花桥镇」→「昆山市」（律师确认单（四）
    第 3 题）、「江苏省徐州市沛县某镇」→「沛县」（补充答复 Q5）；市辖区写「市 + 区」。
    地址省了「市」字的「苏州吴江区……」按「苏州市吴江区」推定。
    推不出返回 None。
    """
    if not address:
        return None
    s = re.sub(r"\s+", "", address)
    s = _LEADING_WORDS_RE.sub("", s, count=1)
    s = _PROVINCE_RE.sub("", s, count=1)
    m = _CITY_DISTRICT_RE.match(s)
    if not m:
        m2 = _CITY_NO_SHI_RE.match(s)
        if m2 and not _FUNCTION_ZONE_RE.search(m2.group(2)):
            return f"{m2.group(1)}市{m2.group(2)}"
        return None
    if m.group(2).endswith(("市", "县")):
        return m.group(2)
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


def _plausible_plaintiff(name: str) -> bool:
    """
    合同乙方像原告：总公司或其分公司（名称含总公司全称），或原告信息表里查得到（独立工程公司）。
    施工单位、监检机构等别的公司不能当原告（律师反馈：系统曾把验收报告上的「施工单位」
    当成合同乙方）。
    """
    return normalize_name(PLAINTIFF_HQ_NAME) in normalize_name(name) or (
        resolve_plaintiff_name(name) is not None
    )


def _source_risk_note(fields: dict, key: str, label: str) -> str:
    """
    把上游字段的风险标记带到派生值上：原文未找到 / OCR 识别 / 其他待核实。
    派生的原告、被告名称直接写进起诉状，不能因为换了一个 src 就丢掉这些提示（宁缺勿造）。
    """
    src = str(_node(fields, key).get("src") or "")
    notes = []
    if "原文未找到" in src:
        notes.append(f"{label}原文未找到，待核实")
    if "OCR" in src or "扫描" in src:
        notes.append("合同为扫描件，OCR识别，请核实")
    if not notes and "待核实" in src:
        notes.append(f"{label}待核实")
    return f"（{'；'.join(notes)}）" if notes else ""


def plaintiff_name_field(fields: dict) -> dict | None:
    """
    原告全称。
    安装合同：以合同盖章页乙方 / 安装方 / 安装单位为准，审批表「合同分公司」只作参考
    （确认单（五）第 1 题：有的地区诉讼由二级、三级分公司提起，合同却是一级分公司签的）。
    合同乙方读不到或不像原告时，才按审批表推出。
    买卖合同：总公司；与合同盖章页乙方不一致时以合同乙方为准并标待核实（补充确认单第 1 题）。
    """
    kind = contract_kind(fields)
    branch_raw = _str_value(fields, "plaintiff_branch_raw")
    derived = derive_plaintiff_name(kind, branch_raw)
    party_b = _compact(_str_value(fields, "contract_party_b"))
    if party_b and not _plausible_plaintiff(party_b):
        party_b_rejected = party_b
        party_b = None
    else:
        party_b_rejected = None

    if kind == "安装" and party_b:
        src = "按律师规则：以合同盖章页乙方为准"
        if derived and derived[0] != party_b:
            src += f"（审批表「合同分公司」推出「{derived[0]}」，仅作参考）"
        return {
            "value": party_b,
            "src": src + _source_risk_note(fields, "contract_party_b", "合同乙方"),
            "channel": "text",
        }

    if derived is None:
        if not party_b:
            return None
        # 审批表「合同分公司」未填（独立工程公司签约时常见）：以合同盖章页乙方为准（第三轮第 12 题）
        return {
            "value": party_b,
            "src": "按律师规则：审批表「合同分公司」未识别，取自合同盖章页乙方 / 安装方名称"
            + _source_risk_note(fields, "contract_party_b", "合同乙方"),
            "channel": "text",
        }
    name, uncertain = derived
    if kind == "买卖":
        src = "按律师规则：买卖合同原告为总公司"
    elif _is_branch(name):
        src = f"按律师规则：合同乙方未识别，暂按审批表「合同分公司」（{branch_raw}）推出，待核实"
    else:
        src = f"按律师规则：合同乙方未识别，审批表「合同分公司」（{branch_raw}）为独立公司，写该公司全称，待核实"
    if kind == "安装" and uncertain:
        src += "；路径中有多个分公司 / 信息表中未找到"
    if party_b_rejected:
        src += f"；合同乙方识别为「{party_b_rejected}」，不像原告，已忽略"
    if party_b and party_b != name:
        return {
            "value": party_b,
            "src": f"按律师规则：以合同盖章页乙方名称为准；与审批表推出的「{name}」不一致，待核实"
            + _source_risk_note(fields, "contract_party_b", "合同乙方"),
            "channel": "text",
        }
    if party_b:
        src += "；与合同盖章页乙方一致"
    return {"value": name, "src": src, "channel": "text"}


# 括号里的备注（审批表「合同买方名称」有时写成「XX公司（某某项目）」）
_TRAILING_NOTE_RE = re.compile(r"[（(][^（）()]*[）)]$")


def defendant_name_field(fields: dict) -> dict | None:
    """
    被告名称：以合同盖章页甲方 / 买方 / 需方 / 发包方为准（律师反馈：审批表「合同买方名称」
    有时多写了括号备注）。合同甲方读不到时沿用抽取到的审批表名称。
    """
    node = _node(fields, "defendant_name")
    approval = node.get("approval_value") if node.get("derived") else node.get("value")
    approval_src = node.get("approval_src") if node.get("derived") else node.get("src")
    party_a = _compact(_str_value(fields, "contract_party_a"))
    if not party_a:
        if not node.get("derived"):
            return None  # 保留抽取值
        if not approval:
            return None
        return {"value": approval, "src": approval_src or "", "approval_value": approval,
                "approval_src": approval_src}
    src = "按律师规则：以合同盖章页甲方 / 买方名称为准"
    approval_c = _compact(str(approval)) if approval else None
    if approval_c and approval_c != party_a:
        stripped = _TRAILING_NOTE_RE.sub("", approval_c)
        if stripped == party_a:
            src += f"（审批表写作「{approval_c}」，括号内为备注）"
        else:
            src += f"；与审批表「{approval_c}」不一致，待核实"
    return {
        "value": party_a,
        "src": src + _source_risk_note(fields, "contract_party_a", "合同甲方"),
        "channel": "text",
        "approval_value": approval,
        "approval_src": approval_src,
    }


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


# 信息表「法定代表人 / 负责人」一栏的写法：
#   只有姓名（不含空白、标点、括号）→ 可加默认职务；
#   「姓名，职务」「姓名、职务」「姓名（职务）」「姓名 常见职务名」→ 已写职务；
#   其余（如「網谷 憲晴」「张三 监事」「张三/董事长」）分不清是带空格的姓名还是职务，
#   保守处理：保留原文、不追加，提醒律师核对（#31 复审）
_NAME_ONLY_RE = re.compile(r"[\w·•]+")
_TITLE_FORMS = (
    re.compile(r"^(.+?)\s*[，,、]\s*(\S.*?)\s*$"),
    re.compile(r"^(.+?)\s*[（(]\s*([^（）()]+?)\s*[)）]\s*$"),
    re.compile(
        r"^(.+?)\s+(\S*(?:董事长|董事|总经理|经理|总裁|负责人|厂长|主任|主席|行长))\s*$"
    ),
)


def _existing_title(person: str) -> str | None:
    """已写职务时返回职务原文；只有姓名返回 None；分不清返回空串。"""
    if _NAME_ONLY_RE.fullmatch(person):
        return None
    for form in _TITLE_FORMS:
        m = form.match(person)
        if m:
            return m.group(2)
    return ""


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
    elif title == "":
        node["src"] += (
            f"；无法判断信息表写法「{person}」是否已含职务，保留原文、未追加"
            f"「{PLAINTIFF_HQ_REP_TITLE}」，待核实"
        )
    elif title != PLAINTIFF_HQ_REP_TITLE:  # 比较完整职务：「副董事长」≠「董事长」
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
    node = {"value": acceptance, "src": src, "channel": "text"}
    # 验收台数来自扫描报告 OCR 计数时，沿用 OCR 通道并提示核实（审核页与起诉状都标待核实）
    base = fields.get("elevator_qty_by_acceptance")
    if isinstance(base, dict) and (
        base.get("channel") == "ocr" or "OCR" in str(base.get("src") or "")
    ):
        node["channel"] = "ocr"
        node["src"] = f"{src}（{base.get('src') or '扫描验收报告'}；OCR识别，请核实）"
    return node


# 移交 / 交付的接收方是物业：「移交甲方指定的物业单位」「交付给物业公司」。接收方须在同一分句内
# 紧跟动词（不跨逗号），「移交甲方后，由物业负责保养」的接收方是甲方；「不 / 无需移交物业」不算
_HANDOVER_TO_PROPERTY_RE = re.compile(r"(?:移交|交付|交接)(?:给|予|至|于)?[^，,。；;、]{0,8}物业")
# 同一分句里接收方前出现否定 / 禁止（「不得移交物业」「无需交付物业」「严禁移交物业」）不算约定移交物业
_NEGATION_RE = re.compile(r"不|无|未|禁止|严禁|勿|非")


def _handover_to_property(text: str) -> bool:
    for clause in re.split(r"[，,。；;]", text):
        for m in _HANDOVER_TO_PROPERTY_RE.finditer(clause):
            if not _NEGATION_RE.search(clause[: m.start()]):
                return True
    return False


def handover_text_field(fields: dict) -> dict:
    """
    「案涉电梯___，被告应按合同约定支付至…」：
    合同约定移交给（甲方指定的）物业 / 物业单位的写「已全部移交物业」，否则写「已全部移交被告」
    （确认单（五）第 6 题）；合同付款条件提到「结算」才加「并办理结算」（补充确认单第 5 题）。
    """
    clause = _str_value(fields, "payment_clause_text") or ""
    handover = _str_value(fields, "handover_clause_text") or ""
    to_property = any(_handover_to_property(t) for t in (handover, clause))
    target = "物业" if to_property else "被告"
    basis = "合同约定移交物业" if to_property else (
        "合同未约定移交物业" if handover or clause else "未识别到移交条款，按移交被告写，待核实"
    )
    if not clause:
        return {
            "value": f"已全部移交{target}",
            "src": f"{basis}；未识别到付款条款，是否写「并办理结算」待核实",
        }
    if "结算" in clause:
        return {"value": f"已全部移交{target}并办理结算", "src": f"{basis}；合同付款条件含「结算」"}
    return {"value": f"已全部移交{target}", "src": f"{basis}；合同付款条件未约定结算"}


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
    location = finest_clause_no(_str_value(fields, "breach_interest_clause_location")) or _MISSING
    text = _to_litigant_terms((_str_value(fields, "breach_clause_text") or _MISSING).rstrip("。；;，, "))
    return {
        "value": f"依据合同{location}约定，{text}。",
        "src": "按律师规则：合同约定违约金，引用违约条款原文（甲方 / 乙方换成被告 / 原告），待核实",
        "channel": "text",
    }


# 「第十条第10.2条」「第10.2条」「10.2条」：取最细一级的数字编号
_DOTTED_CLAUSE_RE = re.compile(r"(\d+(?:\.\d+)+)\s*(?:条|款|项)?")


def finest_clause_no(location: str | None) -> str | None:
    """
    条款号只写最细一级（确认单（五）第 13 题）：「第十条第10.2条」→「第10.2条」。
    没有「10.2」这类分级编号的（如「第十一条第4款」）原样保留，不猜层级。
    """
    if not location:
        return location
    found = _DOTTED_CLAUSE_RE.findall(location)
    if not found or "及" in location or "、" in location:
        return location
    return f"第{found[-1]}条"


def _to_litigant_terms(text: str) -> str:
    """引用违约条款时把合同当事人称谓换成诉讼称谓：甲方 → 被告、乙方 → 原告（第 13 题）。"""
    return text.replace("甲方", "被告").replace("乙方", "原告")


# 能按时间持续计算的标准：既有比率（万分之 / ‰ / % 等），又有计算周期
# （「日万分之五」「年利率6%」「每日按……0.05%」「每逾期一日」「按日」）。
# 只表示触发期限的「逾期超过30日」不算周期；写明「一次性」的一律不算（#30 复审）
_RATE_RE = re.compile(r"万分之|千分之|百分之|‰|%|％|厘")
# 计算周期：「每日」「每逾期一日」「每逾期1天」「每个月」「按日」「日万分之五」「年利率」，
# 以及后缀写法「100元/日」「0.05%/天」「100元每日」
_PERIOD_RE = re.compile(
    r"每(?:逾期|延期|迟延|延迟|拖延)?(?:一|1)?个?[日天月年]|按[日天月年]|[日月年](?:利率|息|万分之|千分之|百分之)"
    r"|[元%％‰]\s*[/／]\s*[日天月年]|元\s*每\s*[日天月年]"
)


_YUAN_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?\s*万?\s*元")


# 有计算周期就按「按期计付」处理，即使另有「一次性」字样：「按每日100元计算，付款时一次性
# 结清」里的「一次性」说的是付款方式，不是只计一次（#36 复审）。只有没有任何计算周期、
# 写「一次性支付 / 逾期超过30日支付5000元」的，才算一次性金额
def _is_periodic_rate(text: str | None) -> bool:
    return bool(text and _RATE_RE.search(text) and _PERIOD_RE.search(text))


def _is_periodic_amount(text: str | None) -> bool:
    """按期计付的固定金额（「每逾期一日支付违约金100元」「100元/日」），不是一次性总额。"""
    return bool(text and _YUAN_RE.search(text) and _PERIOD_RE.search(text))


def _is_periodic(text: str | None) -> bool:
    return _is_periodic_rate(text) or _is_periodic_amount(text)


def is_lump_sum_penalty(fields: dict) -> bool:
    """
    合同约定的违约金没有按日 / 月 / 年计算的标准（固定金额、一次性比例或未识别到），
    诉请改写为「支付违约金¥X元」、不写「以……为基数……计至实际付清之日止」
    （律师确认单（四）第 1 题）。律师改过计算标准时以律师为准，不切换。
    """
    if _lawyer_edited(fields.get("interest_rate_basis")):
        return False
    breach = _str_value(fields, "breach_interest_rate_text")
    clause = _str_value(fields, "breach_clause_text")
    # 按日 / 年持续计付的（比率或每期固定金额），以及条款原文按期计算、只是费率没抽出来的，
    # 都不算一次性金额（见 interest_basis_field）
    return (
        _breach_is_penalty(fields)
        and not _is_periodic(breach)
        and not _is_periodic(clause)
    )


def interest_basis_field(fields: dict) -> dict:
    """
    合同有甲方逾期付款的利率约定就用约定（待核实措辞），否则 LPR 常规话术。
    违约金为固定金额 / 一次性比例时不按利率计算（诉请直接写金额，见 penalty_amount_field）。
    """
    breach = _str_value(fields, "breach_interest_rate_text")
    if is_lump_sum_penalty(fields):
        return {
            "value": "不适用（违约金为固定金额或一次性比例，诉请直接写金额）",
            "src": "按律师规则：合同约定固定金额 / 一次性比例违约金，不按利率计算",
        }
    if is_daily_amount_penalty(fields):
        return {
            "value": "不适用（违约金按每日固定金额计算，诉请写金额与起算日）",
            "src": "按律师规则：合同约定按日计付的固定金额违约金，不按利率计算",
        }
    if _breach_is_penalty(fields) and not _is_periodic_rate(breach):
        # 条款按期计付但没有可直接套用的费率（费率没抽出来，或是「每日100元」这类每期固定金额，
        # 写法尚未经律师确认）：不套用 LPR，也不当一次性金额，留给律师填
        clause = _str_value(fields, "breach_clause_text")
        if _is_periodic_amount(breach) or _is_periodic_amount(clause):
            src = "合同约定按期计付的固定金额违约金（如每日若干元），写法待律师确认，不套用 LPR"
        else:
            src = "合同按日 / 年计违约金，但未识别出约定费率，请律师填写，不套用 LPR"
        return {"value": None, "src": src}
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


# 违约金 / 利息上限：「逾期付款违约金最高不超过安装费总额的10%」→「，最高不超过安装费总额的10%」
# 逗号断句，但金额里的千分位逗号（「10,000元」「10，000元」）不算断句
_CAP_RE = re.compile(r"最高(?:不得?超过|为)(?:[^。；;，,]|(?<=\d)[,，](?=\d{3})){1,40}")


def penalty_cap_field(fields: dict) -> dict:
    """
    诉请括号「……计至实际付清之日止」后补写合同约定的上限（新案卷核对：律师诉状写
    「按每日万分之二的利率计算至实际付清之日止，最高不超过安装费总额的10%」）。
    只在按合同约定费率计付时写；一次性 / 按日固定金额、LPR 常规话术不写；律师已把上限写进
    计算标准时不重复。
    """
    none = {"value": "", "src": "合同违约条款未约定上限，或不按合同费率计算"}
    basis = _node(fields, "interest_rate_basis")
    basis_value = str(basis.get("value") or "")
    if (
        is_lump_sum_penalty(fields)
        or is_daily_amount_penalty(fields)
        or not basis_value
        or basis_value == LPR_INTEREST_BASIS
        or "最高" in basis_value
    ):
        return none
    for key in ("breach_clause_text", "breach_interest_rate_text"):
        text = re.sub(r"\s+", "", _str_value(fields, key) or "")
        m = _CAP_RE.search(text)
        if m and m.group(0)[-1].isdigit() and text[m.end() : m.end() + 1] in (",", "，"):
            # 数字后紧跟逗号又不像千分位（「1,5」）：分不清上限写到哪，宁缺勿造
            return {"value": None, "src": "合同约定了上限，但写法无法完整识别，请律师填写"}
        if m:
            return {
                "value": f"，{m.group(0)}",
                "src": "按合同违约条款约定的上限，待核实",
                "channel": "text",
            }
    return none


# 违约金金额：「人民币5000元」「5,000.00元」「1.5万元」；比例：「合同总价的10%」「欠款的5%」
_PENALTY_YUAN_RE = re.compile(r"([0-9][0-9,，]*(?:\.\d+)?)\s*(万)?\s*元")
_PENALTY_RATIO_RE = re.compile(
    r"(合同总价款?|合同总额|合同价款|合同金额|总价款?|欠款|未付款项?|逾期付款金额|逾期款项?|"
    r"应付未付(?:款项|金额)?)(?:金额)?的?\s*(\d+(?:\.\d+)?)\s*[%％]"
)
_UNPAID_BASES = ("欠款", "未付款", "逾期", "应付未付")
_ANY_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
# 上下限 / 封顶等附加条件：只按单一标准算会出错，交律师（#36 复审）。
# 分档（「30日内5%，超过30日10%」）会出现多个比例 / 金额，由下面的计数拦下；
# 「逾期超过30日」单独出现只是触发期限，不算分档
_PENALTY_LIMIT_RE = re.compile(
    r"最高|最低|不超过|不高于|不低于|不少于|上限|下限|封顶|为限|累计|分别"
)


def _ratio_base_key(base_word: str) -> str:
    """比例的计算基数归为两类：未付金额 / 合同总价（「合同总价」「合同总价款」是同一基数）。"""
    return "unpaid_amount" if base_word.startswith(_UNPAID_BASES) else "total_amount"


def _penalty_ambiguity(text: str) -> str | None:
    """违约金条款不是单一可确定标准时返回原因；单一固定金额或单一比例返回 None。"""
    if _PENALTY_LIMIT_RE.search(text):
        return "条款含上限 / 下限或分档等附加条件"
    # 按「基数 + 比例」比较完整标准：「欠款的5%」与「合同总价的5%」是两档，不能只按 5 去重；
    # 利率摘录与条款原文重复写同一标准时，归一后是同一项，不算分档
    standards = {
        (_ratio_base_key(m.group(1)), float(m.group(2)))
        for m in _PENALTY_RATIO_RE.finditer(text)
    }
    if len(standards) > 1:
        return "条款出现多个计算标准（可能分档）"
    pcts = {float(p) for p in _ANY_PERCENT_RE.findall(text)}
    yuans = {
        float(num.replace(",", "").replace("，", "")) * (10000 if wan else 1)
        for num, wan in _PENALTY_YUAN_RE.findall(text)
    }
    if len(pcts) > 1 or len(yuans) > 1:
        return "条款出现多个比例或金额（可能分档）"
    if pcts and yuans:
        return "条款同时出现比例与金额"
    if pcts and not _PENALTY_RATIO_RE.search(text):
        return "条款写了比例，但计算基数无法识别"
    return None


def _penalty_from_ratio(fields: dict, text: str) -> tuple[float, str] | None:
    m = _PENALTY_RATIO_RE.search(text)
    if not m:
        return None
    base_word, pct = m.group(1), float(m.group(2))
    base_key = _ratio_base_key(base_word)
    base = parse_amount(_node(fields, base_key).get("value"))
    if base is None:
        return None
    label = "未付金额" if base_key == "unpaid_amount" else "合同总价"
    note = f"按条款「{base_word}的{pct:g}%」计算：{label} {_plain_number(base)} × {pct:g}%"
    return round(base * pct / 100, 2), note


def _penalty_fixed(text: str) -> float | None:
    """条款里只出现一个金额时取它；出现多个（如分档违约金）分不清，不猜。"""
    hits = {
        float(num.replace(",", "").replace("，", "")) * (10000 if wan else 1)
        for num, wan in _PENALTY_YUAN_RE.findall(text)
    }
    return hits.pop() if len(hits) == 1 else None


def penalty_amount_field(fields: dict) -> dict | None:
    """
    固定金额 / 一次性比例违约金的金额（律师确认单（四）第 1 题）：条款写明金额的照抄，
    写比例的按合同总价或未付金额算出；一律标待核实，提醒办案律师核对。算不出时留【待补充】。
    不是这类违约金时不派生。
    """
    if is_daily_amount_penalty(fields):
        return _daily_penalty_amount(fields)
    if not is_lump_sum_penalty(fields):
        return None
    text = "；".join(
        t
        for t in (
            _str_value(fields, "breach_interest_rate_text"),
            _str_value(fields, "breach_clause_text"),
        )
        if t
    )
    reason = _penalty_ambiguity(text)
    if reason:
        return {
            "value": None,
            "src": f"合同约定一次性违约金，但{reason}，系统不自动计算，请律师填写",
        }
    ratio = _penalty_from_ratio(fields, text)
    if ratio:
        amount, note = ratio
        return {
            "value": amount,
            "src": f"按律师规则：一次性比例违约金，{note}，待核实",
            "channel": "text",
        }
    fixed = _penalty_fixed(text)
    if fixed is not None:
        return {
            "value": fixed,
            "src": "按律师规则：合同约定固定金额违约金，照抄约定金额，待核实",
            "channel": "text",
        }
    return {
        "value": None,
        "src": "合同约定固定金额 / 一次性比例违约金，但未能确定金额，请律师填写",
    }


# ── 按日计付的固定金额违约金（补充答复 Q6）───────────────────────────────────
# 诉请写「支付违约金¥X元（自A起，按照每日N元计至实际付清之日止，暂计至B）」：
#   A = 按付款条件推定的起算日（付款期限届满的次日）；B = 生成起诉状当日；
#   X = 每日金额 × A 至 B 的天数（头尾都算）。A、X 一律标待核实。
# 只处理按「日 / 天」计的固定金额；按月 / 年计的仍留【待补充】。
_DAILY_RE = re.compile(
    r"每(?:逾期)?(?:一|1)?[日天]|按[日天]|元\s*[/／]\s*[日天]|元\s*每\s*[日天]"
)
_CN_TZ = timezone(timedelta(hours=8))
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
# 付款期限：「验收合格后30日内」「验收合格之日起两个月内」「验收合格后30个工作日内」
_DUE_AFTER_ACCEPTANCE_RE = re.compile(
    r"验收合格(?:之日)?(?:后|起)的?\s*([0-9]+|[零一二两三四五六七八九十百]+)\s*个?\s*"
    r"(工作日|日历日|自然日|日|天|月)内?"
)
# 条款里任何「N 日 / N 天 / N 个月 / N 个工作日」期限（不论写在哪个付款节点）
_DEADLINE_RE = re.compile(
    r"(?:[0-9]+|[零一二两三四五六七八九十百]+)\s*个?\s*(?:工作日|日历日|自然日|日|天|月)"
)
# 质保期：「二年」「2年」「24个月」「贰年」
_PERIOD_TEXT_RE = re.compile(r"([0-9]+|[零一二两三四五六七八九十贰叁]+)\s*个?\s*(年|月)")


def _today() -> date:
    """起诉状生成当日（北京时间）；单独成函数便于测试固定日期。"""
    return datetime.now(_CN_TZ).date()


def _cn_int(text: str) -> int | None:
    """「30」「三十」「二十四」「两」「贰」→ 整数；认不出返回 None。"""
    text = text.replace("贰", "二").replace("叁", "三")
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        tens, _, ones = text.partition("十")
        t = _CN_DIGITS.get(tens, None) if tens else 1
        o = _CN_DIGITS.get(ones, None) if ones else 0
        return None if t is None or o is None else t * 10 + o
    return _CN_DIGITS.get(text) if len(text) == 1 else None


def _add_months(d: date, months: int) -> date:
    year, month = divmod(d.month - 1 + months, 12)
    year += d.year
    day = min(d.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def _is_daily_amount(text: str | None) -> bool:
    return bool(
        text and _YUAN_RE.search(text) and _DAILY_RE.search(text) and not _RATE_RE.search(text)
    )


def is_daily_amount_penalty(fields: dict) -> bool:
    """违约金按「每日 N 元」计付（不是比率），律师没改过计算标准。"""
    if _lawyer_edited(fields.get("interest_rate_basis")) or not _breach_is_penalty(fields):
        return False
    breach = _str_value(fields, "breach_interest_rate_text")
    clause = _str_value(fields, "breach_clause_text")
    if _is_periodic_rate(breach) or _is_periodic_rate(clause):
        return False
    return _is_daily_amount(breach) or _is_daily_amount(clause)


def _debt_is_retention(fields: dict) -> bool:
    """诉请的欠款就是审批表的未付质保金。"""
    unpaid = parse_amount(_node(fields, "unpaid_amount").get("value"))
    retention = parse_amount(_node(fields, "retention_unpaid_amount").get("value"))
    return bool(unpaid and retention and abs(unpaid - retention) <= 0.01)


def needs_warranty_period(fields: dict) -> bool:
    """
    按需 OCR 是否还要找质保期：欠款是质保金、质保期没读到，且违约金可能按每日固定金额计
    （违约条款还没读到，或已确认按日计）——这时起算日要用验收日 + 质保期推算（补充答复 Q6）。
    """
    if _str_value(fields, "warranty_period") or not _debt_is_retention(fields):
        return False
    no_breach = not (
        _str_value(fields, "breach_interest_rate_text")
        or _str_value(fields, "breach_clause_text")
    )
    return no_breach or is_daily_amount_penalty(fields)


def _daily_rate(fields: dict) -> float | None:
    """每日金额：条款只写了一个金额、没有上限 / 分档等附加条件时才取。"""
    text = "；".join(
        t
        for t in (
            _str_value(fields, "breach_interest_rate_text"),
            _str_value(fields, "breach_clause_text"),
        )
        if t
    )
    if _penalty_ambiguity(text):
        return None
    return _penalty_fixed(text)


def _penalty_start(fields: dict) -> tuple[date | None, str]:
    """
    违约金起算日 = 欠款付款期限届满的次日：
      欠款是质保金 → 最晚验收合格日 + 质保期，再加一天（律师例：2023-11-19 验收、保修二年 →
      2025-11-20 起算）；
      否则按付款条款「验收合格后 N 日 / N 个月内」→ 最晚验收合格日 + N，再加一天。
    按工作日计、条款里有多个不同期限、缺验收日期等推不出时返回 None 和原因。
    """
    acc = parse_date(_str_value(fields, "acceptance_latest_date"))
    if acc is None:
        return None, "缺最晚验收合格日期，无法推定起算日"
    accepted = date(*acc)
    if _debt_is_retention(fields):
        return _retention_due(fields, accepted)
    clause = _str_value(fields, "payment_clause_text") or ""
    # 只处理「整个付款条款只有一个付款节点，且就是验收合格后 N 日 / 月内」的情况：
    # 按整条条款数付款动作、期限和比例（逗号连接的多期付款也算多个节点），多于一个就不推算；
    # 移交 / 结算 / 发票等前置条件写在条款任何位置（包括单独成句）也不推算（#37 复审）
    if _PREREQUISITE_RE.search(clause):
        return None, "付款条件还含移交 / 结算等前置条件，系统不推算，请律师填写起算日"
    if (
        len(_PAY_VERB_RE.findall(clause)) != 1
        or len(_DEADLINE_RE.findall(clause)) != 1
        or len(set(_ANY_PERCENT_RE.findall(clause))) > 1
    ):
        return None, "付款条款有多个付款节点或未找到付款节点，无法确定本次欠款的期限，请律师填写起算日"
    terms = {(_cn_int(n), unit) for n, unit in _DUE_AFTER_ACCEPTANCE_RE.findall(clause)}
    if any(unit == "工作日" for _, unit in terms):
        return None, "付款期限按工作日计，系统不推算，请律师填写起算日"
    if len(terms) != 1:
        return None, "付款条款中未找到唯一的「验收合格后 N 日 / 月内」期限，请律师填写起算日"
    (n, unit), = terms
    if not n:
        return None, "付款期限的天数无法识别，请律师填写起算日"
    due = _add_months(accepted, n) if unit == "月" else accepted + timedelta(days=n)
    label = f"{n}个月" if unit == "月" else f"{n}日"
    return due + timedelta(days=1), (
        f"最晚验收合格日 {accepted.isoformat()} + 付款期限「验收合格后{label}内」的次日"
    )


_PAY_VERB_RE = re.compile(r"支付|付清|付款|付至|付\s*\d|付剩余|付余款|返还|结清")
_PREREQUISITE_RE = re.compile(r"移交|结算|审计|发票|竣工|准运|备案")
# 质保期的起算点不是验收合格时（「自交付使用之日起」等），按验收日算会错
_NON_ACCEPTANCE_START_RE = re.compile(r"交付|使用|移交|竣工|安装完成|签收|开具|备案")
# 质保金返还期限：「质保期满后30日内返还」「质保期届满之日起两个月内」
_RETENTION_GRACE_RE = re.compile(
    r"质保期(?:满|届满|结束)(?:之日)?(?:后|起)的?\s*([0-9]+|[零一二两三四五六七八九十百]+)\s*个?\s*"
    r"(工作日|日历日|自然日|日|天|月)内?"
)
_RETENTION_DUE_RE = re.compile(r"质保期(?:满|届满|结束)")
# 返还动作：条款里根本没写返还 / 支付，读不出返还条件
_RETENTION_PAY_RE = re.compile(r"返还|退还|支付|付清|结清|付")
# 质保期起算点的写法（「自工程竣工验收合格之日起」）：说的是质保期从哪天算，不是返还条件
_START_POINT_RE = re.compile(r"[自从][^，,。；;]{0,30}?之日起(?:计算|计)?")
# 起算点不是验收合格（交付使用、移交等）时按验收日算会错
_NON_ACCEPTANCE_POINT_RE = re.compile(r"交付|使用|移交|安装完成|签收|开具|备案")
# 质保期本身（「质保期24个月」「质保期为二年」）不是返还期限
_PERIOD_STATEMENT_RE = re.compile(
    r"(?:质保|保修)期限?(?:为|是)?\s*(?:[0-9]+|[零一二两三四五六七八九十贰叁]+)\s*个?\s*(?:年|月)"
)
# 已解析的返还期限：须紧跟「质保期满」（「质保期满，经甲方确认后十五日内」不算）
_RETENTION_GRACE_AT_DUE_RE = re.compile(
    r"质保期(?:满|届满|结束)(?:之日)?(?:后|起)的?\s*(?:[0-9]+|[零一二两三四五六七八九十百]+)\s*个?\s*"
    r"(?:工作日|日历日|自然日|日|天|月)内?"
)
_TIME_UNIT_RE = re.compile(r"[年月日天周季]|星期")
# 质保金返还特有的前置条件（付款条款共用的移交 / 结算 / 发票之外）
_RETENTION_PREREQ_RE = re.compile(r"确认|审核|审批|申请|考核|无质量问题|无遗留问题")


def _retention_condition_text(clause: str) -> str:
    """去掉质保期起算点写法后的条款：前置条件、未解析期限都在整条条款上检查（#37 复审）。"""
    return _START_POINT_RE.sub("", clause)


def _retention_unparsed_deadline(clause: str) -> bool:
    """
    整条质保金条款里，除质保期本身和紧跟「质保期满」的已解析期限外还有时间字样
    （「一年内」「返还应在30日内完成」「经甲方确认后十五日内」）时返回 True：
    不能因为正则没认出就当作期满即返还（#37 复审）。
    """
    rest = _PERIOD_STATEMENT_RE.sub("", _retention_condition_text(clause))
    rest = _RETENTION_GRACE_AT_DUE_RE.sub("质保期满", rest).replace("之日", "")
    return bool(_TIME_UNIT_RE.search(rest))


def _retention_due(fields: dict, accepted: date) -> tuple[date | None, str]:
    """
    欠款为质保金：到期日 = 质保期满日 + 质保金返还期限（条款写「质保期满后无息返还」时为 0）。
    质保期须自验收合格之日起算；返还条件读不清（没写质保期满、分期返还、有结算等前置条件、
    按工作日计）时不推算（#37 复审：律师的例子不能推广到所有返还条件）。
    """
    period_text = _str_value(fields, "warranty_period") or ""
    m = _PERIOD_TEXT_RE.search(period_text)
    n = _cn_int(m.group(1)) if m else None
    if not n:
        return None, "欠款为质保金，但未识别出质保期，无法推定起算日"
    period_src = str(_node(fields, "warranty_period").get("src") or "")
    if _NON_ACCEPTANCE_START_RE.search(period_src + period_text):
        return None, "质保期不是自验收合格之日起算，系统不推算，请律师填写起算日"
    clause = _str_value(fields, "retention_clause_text") or ""
    if not _RETENTION_DUE_RE.search(clause) or not _RETENTION_PAY_RE.search(clause):
        return None, "未识别出「质保期满后返还」的质保金返还条件，请律师填写起算日"
    if any(_NON_ACCEPTANCE_POINT_RE.search(p) for p in _START_POINT_RE.findall(clause)):
        return None, "质保期不是自验收合格之日起算，系统不推算，请律师填写起算日"
    # 前置条件在整条条款上检查（写在「质保期满」前面的「须经结算确认后」也算），只剔除
    # 「自竣工验收合格之日起」这类质保期起算写法（#37 复审）
    conditions = _retention_condition_text(clause)
    if (
        len(retention_stages(fields)) > 1
        or _PREREQUISITE_RE.search(conditions)
        or _RETENTION_PREREQ_RE.search(conditions)
    ):
        return None, "质保金分期返还或带结算等前置条件，系统不推算，请律师填写起算日"
    grace = {(_cn_int(g), unit) for g, unit in _RETENTION_GRACE_RE.findall(clause)}
    if any(unit == "工作日" for _, unit in grace) or len(grace) > 1:
        return None, "质保金返还期限按工作日计或有多个期限，系统不推算，请律师填写起算日"
    if _retention_unparsed_deadline(clause):
        return None, "质保金返还条款写了期限但系统无法确定，请律师填写起算日"
    months = n * 12 if m.group(2) == "年" else n
    due = _add_months(accepted, months)
    grace_note = ""
    if grace:
        (g, unit), = grace
        if not g:
            return None, "质保金返还期限无法识别，请律师填写起算日"
        due = _add_months(due, g) if unit == "月" else due + timedelta(days=g)
        grace_note = f" + 返还期限「{g}{'个月' if unit == '月' else '日'}」"
    return due + timedelta(days=1), (
        f"欠款为质保金：最晚验收合格日 {accepted.isoformat()} + 质保期「{period_text}」"
        f"{grace_note}的次日"
    )


def penalty_daily_text_field(fields: dict) -> dict | None:
    """诉请括号里的「按照每日N元」。"""
    if not is_daily_amount_penalty(fields):
        return None
    rate = _daily_rate(fields)
    if rate is None:
        return {"value": None, "src": "未能确定每日违约金金额（可能有上限或分档），请律师填写"}
    return {
        "value": f"每日{_plain_number(rate)}元",
        "src": "合同约定的每日违约金金额，待核实",
        "channel": "text",
    }


def penalty_start_date_field(fields: dict) -> dict | None:
    if not is_daily_amount_penalty(fields):
        return None
    start, note = _penalty_start(fields)
    if start is None:
        return {"value": None, "src": note}
    return {
        "value": start.isoformat(),
        "src": f"按律师规则：{note}，待核实",
        "channel": "text",
    }


def penalty_until_date_field(fields: dict) -> dict | None:
    """「暂计至」= 生成起诉状当日。每次生成都按当天刷新。"""
    if not is_daily_amount_penalty(fields):
        return None
    return {"value": _today().isoformat(), "src": "按律师规则：暂计至起诉状生成当日"}


def _final_date(fields: dict, key: str) -> date | None:
    parsed = parse_date(_str_value(fields, key))
    return date(*parsed) if parsed else None


def _daily_penalty_amount(fields: dict) -> dict:
    """
    违约金暂计金额 = 每日金额 × 起算日至暂计日的天数（头尾都算）。
    三项都取最终生效的审核字段（penalty_daily_text / penalty_start_date / penalty_until_date）：
    律师改了其中任何一项，金额按律师的值重算，不沿用规则推出的旧条件（#37 复审）。
    """
    rate_text = _str_value(fields, "penalty_daily_text")
    rates = {
        float(num.replace(",", "").replace("，", "")) * (10000 if wan else 1)
        for num, wan in _PENALTY_YUAN_RE.findall(rate_text or "")
    }
    if rate_text and not rates:
        bare = parse_amount(rate_text)  # 律师只填了数字「200」
        rates = {bare} if bare is not None else set()
    start = _final_date(fields, "penalty_start_date")
    until = _final_date(fields, "penalty_until_date")
    if len(rates) != 1:
        return {"value": None, "src": "未能确定每日违约金金额，请律师填写或核对「按日违约金标准」"}
    if start is None:
        note = str(_node(fields, "penalty_start_date").get("src") or "起算日未确定")
        return {"value": None, "src": note}
    if until is None:
        return {"value": None, "src": "「暂计至」日期无法识别，请律师核对"}
    rate = rates.pop()
    days = (until - start).days + 1
    if days <= 0:
        return {
            "value": None,
            "src": f"违约金起算日 {start.isoformat()} 晚于暂计日 {until.isoformat()}，"
            "尚未产生违约金，请律师核对",
        }
    return {
        "value": round(rate * days, 2),
        "src": (
            f"按律师规则：每日{_plain_number(rate)}元 × {days}天"
            f"（{start.isoformat()} 至 {until.isoformat()}，头尾都算），待核实"
        ),
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
    """「仅支付了¥X元」后的括注：比例默认不写（确认单（五）第 4 题）。"""
    # 确认单（五）第 4 题：比例默认不写，需要时由办案律师自行添加
    return {"value": "", "src": "按律师规则：默认不写占合同款比例，需要时律师自行添加"}


def unpaid_note_field(fields: dict) -> dict:
    """「尚欠…¥Y元」后的括注：已付 + 欠款 ≠ 总价时写算式「（到期金额-已付）」，否则不写比例。"""
    mode = _ratio_mode(fields)
    total, paid, unpaid = _amount_parts(fields)
    if mode is None or paid is None or unpaid is None:
        return {"value": "", "src": "已付或欠款金额缺失，不写比例 / 算式"}
    if mode and total:
        # 确认单（五）第 4 题：比例默认不写，需要时由办案律师自行添加
        return {"value": "", "src": "按律师规则：默认不写占合同款比例，需要时律师自行添加"}
    return {
        "value": f"（{_plain_number(paid + unpaid)}-{_plain_number(paid)}）",
        "src": "按律师规则：已付 + 欠款 ≠ 合同总价，写算式（到期金额-已付款），待核实",
    }


def payable_amount_field(fields: dict) -> dict | None:
    """
    「被告应按合同约定支付至100%合同款¥X元」的金额（确认单（五）第 5 题）：
    X = 已付 + 欠款（本次起诉已到期的合同款）。支付至 100% 时应等于合同总价，不等标待核实。
    """
    total, paid, unpaid = _amount_parts(fields)
    if paid is None or unpaid is None:
        return None
    due = paid + unpaid
    src = "按律师规则：已付 + 欠款"
    ratio = parse_percent(_str_value(fields, "payable_ratio"))
    if total and ratio is not None and abs(total * ratio / 100 - due) > 0.01:
        src += f"；与合同总价 × {ratio:g}% 不一致，待核实"
    return {"value": _plain_number(due), "src": src}


def debt_term_field(fields: dict) -> dict:
    """
    诉请第 1 项「判令被告向原告支付___¥X元」（确认单（五）第 9 题）：
    已付 + 欠款 = 合同总价 →「剩余合同款」；否则「到期合同款」。
    已付 + 欠款大于合同总价（可能调过价 / 结算价不同）或金额缺失时，标待核实交律师判断。
    """
    total, paid, unpaid = _amount_parts(fields)
    if total is None or paid is None or unpaid is None:
        return {"value": "剩余合同款", "src": "合同总价、已付或欠款金额缺失，按「剩余合同款」写，待核实"}
    diff = paid + unpaid - total
    if abs(diff) <= 0.01:
        return {"value": "剩余合同款", "src": "按律师规则：已付 + 欠款 = 合同总价"}
    if diff < 0:
        return {"value": "到期合同款", "src": "按律师规则：已付 + 欠款 < 合同总价"}
    return {
        "value": "到期合同款",
        "src": "已付 + 欠款大于合同总价（可能调过价或结算价与原价不同），请律师判断，待核实",
    }


def _retention_in_debt(fields: dict) -> bool:
    """审批表「未付款项构成」里质保金大于 0：本次欠款含质保金。"""
    amount = parse_amount(_str_value(fields, "retention_unpaid_amount"))
    return amount is not None and amount > 0.005


def warranty_expiry_field(fields: dict) -> dict:
    """
    欠款含质保金时写「，质保期已于X年X月X日到期」（确认单（五）第 7 题）：
    到期日 = 最晚验收合格日 + 质保期，标待核实。质保期不自验收起算、读不到、
    或还没到期时不写，在出处里说明原因。
    """
    if not _retention_in_debt(fields):
        return {"value": "", "src": "欠款不含质保金，不写质保期到期句"}
    accepted = parse_date(_str_value(fields, "acceptance_latest_date"))
    period_text = _str_value(fields, "warranty_period") or ""
    m = _PERIOD_TEXT_RE.search(period_text)
    months = None
    if m and _cn_int(m.group(1)):
        n = _cn_int(m.group(1))
        months = n * 12 if m.group(2) == "年" else n
    if not accepted or not months:
        return {"value": "", "src": "欠款含质保金，但验收日期或质保期未识别，质保期到期句请律师补写"}
    period_src = str(_node(fields, "warranty_period").get("src") or "")
    start = period_src + period_text
    note = ""
    if _NON_ACCEPTANCE_START_RE.search(start):
        # 起算点也提到验收 / 合格证（如「获得检验合格证并正式移交甲方之日起」）：律师认可按最晚验收日
        # 推算并标黄核实（确认单（五）第 7 题）；完全与验收无关的（如交付使用之日）才不推算
        if not _ACCEPTANCE_START_RE.search(start):
            return {"value": "", "src": "质保期不是自验收合格之日起算，到期日请律师补写"}
        note = "（合同起算点还含移交等条件，按验收日推算）"
    expiry = _add_months(date(*accepted), months)
    when = f"{expiry.year}年{expiry.month}月{expiry.day}日"
    if expiry > _today():
        return {"value": "", "src": f"欠款含质保金，但质保期到 {when} 才届满，请律师核对诉请"}
    return {
        "value": f"，质保期已于{when}到期",
        "src": f"按律师规则：最晚验收合格日 + 质保期「{period_text}」推算{note}，待核实",
    }


_ACCEPTANCE_START_RE = re.compile(r"验收|合格证|检验合格")


def contract_total_stated_field(fields: dict) -> dict | None:
    """
    「合同总价为¥X元」：一般就是审批表合同总额；有补充协议时写合同正文的原价
    （确认单（五）第 8 题：再加一句「双方签订补充协议，确认合同总价变更为…」）。
    """
    total = _node(fields, "total_amount")
    if not _str_value(fields, "supplement_agreement"):
        if total.get("value") in (None, ""):
            return None
        return {k: v for k, v in total.items() if k != "derived"}
    original = _node(fields, "contract_total_in_contract")
    if parse_amount(original.get("value")) is None:
        return {"value": None, "src": "有补充协议：合同正文的原合同总价未识别，请律师填写"}
    return {
        "value": _plain_number(parse_amount(original.get("value"))),
        "src": f"有补充协议：合同正文约定的原合同总价（{original.get('src') or '合同'}），待核实",
    }


def supplement_sentence_field(fields: dict) -> dict:
    """
    有补充协议时：「后双方签订补充协议，确认合同总价变更为¥X元。」X 为审批表合同总额。
    补充协议可能改单价、数量、规格、税率，表述一律标待核实，由律师确认（确认单（五）第 8 题）。
    """
    files = _str_value(fields, "supplement_agreement")
    total = parse_amount(_node(fields, "total_amount").get("value"))
    if not files:
        return {"value": "", "src": "未识别到补充协议"}
    if total is None:
        return {"value": "后双方签订补充协议，确认合同总价变更为¥【待补充】元。",
                "src": f"识别到补充协议 {files}；合同总额未识别，请律师核对表述，待核实"}
    return {
        "value": f"后双方签订补充协议，确认合同总价变更为¥{_plain_number(total)}元。",
        "src": f"按律师规则：识别到补充协议 {files}，表述请律师核对（可能调整单价、数量、规格或税率），待核实",
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
        return ("defendant", *_defendant_seat_court(fields, "约定被告所在地法院"))
    if _SIGN_PLACE_RE.search(dispute) and not _SITE_RE.search(dispute):
        # 签订地取合同盖章页「合同签订地」栏（新案卷核对：律师按它写法院），推断结果待核实
        place, conflict = _sign_place(fields)
        if conflict:
            return "sign_place", None, conflict
        district = derive_court_district(place)
        if district:
            return "sign_place", district, f"按合同签订地「{place}」推定，待核实管辖"
        if place:
            return "sign_place", None, f"合同签订地「{place}」未写明区县，请律师确定管辖法院"
        return "sign_place", None, "约定合同签订地法院，合同签订地未识别，请律师确定管辖法院"
    if _SITE_RE.search(dispute) or not dispute:
        return _site_jurisdiction(fields, delivery=bool(_DELIVERY_RE.search(dispute)))
    return ("general", *_defendant_seat_court(fields, "未约定具体地点"))


def _sign_place(fields: dict) -> tuple[str | None, str | None]:
    """
    合同签订地：盖章页「合同签订地」优先，没有取封面「合同订立地点」。两处都写了、
    却推出不同的法院时返回说明，交律师判断（确认单（五）第 15 题）。
    """
    stamp = _str_value(fields, "contract_sign_place")
    cover = _str_value(fields, "contract_sign_place_cover")
    if stamp and cover and _compact(stamp) != _compact(cover):
        if derive_court_district(stamp) != derive_court_district(cover):
            return stamp, f"合同盖章页签订地「{stamp}」与封面订立地点「{cover}」不一致，请律师判断管辖法院"
    return stamp or cover, None


def _defendant_seat_court(fields: dict, basis: str) -> tuple[str | None, str]:
    """
    被告住所地法院：先按被告住址（工商登记）推定；还没查工商时，按审批表买方地址 / 合同甲方地址
    （`defendant_address_ref`，不写进起诉状）推断并标待核实（新案卷核对）。
    """
    addr = _str_value(fields, "defendant_address")
    district = derive_court_district(addr)
    if district:
        return district, f"{basis}，按被告住址推定，待核实"
    ref = _str_value(fields, "defendant_address_ref")
    district = derive_court_district(ref)
    if district:
        return district, f"{basis}，被告住址（工商登记）未填，系统按「{ref}」推断，待核实"
    return None, f"{basis}，按被告住址推定，请律师确定管辖法院"


def _seat_ref_note(fields: dict) -> str:
    """法院按被告参考地址推断时，管辖句里直接写明依据；律师改过法院或已填被告住址则不加。"""
    if _lawyer_edited(fields.get("court_district")):
        return ""
    if derive_court_district(_str_value(fields, "defendant_address")):
        return ""
    ref = _str_value(fields, "defendant_address_ref")
    if not derive_court_district(ref):
        return ""
    return f"【系统按被告地址「{ref}」推断，待核实】"


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


def _inferred_court_note(fields: dict, place: str | None) -> str:
    """
    法院辖区是按验收报告安装地点推断的（约定地点没写区县）时，在管辖句里直接写明依据，
    让律师在文书里就看到这是系统推断、不是合同约定（#29 复审）。律师改过法院则不加。
    """
    court = fields.get("court_district")
    if _lawyer_edited(court) or derive_court_district(place):
        return ""
    install = _str_value(fields, "install_address")
    if not (install and derive_court_district(install)):
        return ""
    return f"【系统按验收报告安装地点「{install}」推断，待核实】"


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
            f"因{label}为{place or _MISSING}，属{court}法院辖区"
            f"{_inferred_court_note(fields, place)}，故原告向{court}人民法院提起诉讼。"
        )
    elif mode == "general":
        text = (
            f"{_GENERAL_JURISDICTION_BASIS}{_seat_ref_note(fields)}，"
            f"故原告向{court}人民法院提起诉讼。"
        )
    elif mode == "sign_place":
        place = _sign_place(fields)[0] or _MISSING
        text = f"合同签订地为{place}，故原告向{court}人民法院提起诉讼。"
    elif mode == "defendant":
        text = f"{_seat_ref_note(fields)}故原告向{court}人民法院提起诉讼。"
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


# 「质保金」条款实为乙方另开银行质保保函（不从合同款里扣留）：如「同时乙方向甲方开具安装结算
# 金额5%，有效期至质保期满的银行质保保函」。条款里同时写了预留 / 扣留等字样时仍按质保金处理
# 正向确认：须写明「乙方（向甲方）开具 / 提供 / 出具 / 提交……保函」
_GUARANTEE_ISSUE_RE = re.compile(r"乙方[^。；;]{0,15}?(?:开具|提供|出具|提交)[^。；;]{0,40}?保函")
# 否定保函（「不采用银行保函」「无需提供保函」）
_GUARANTEE_NEGATED_RE = re.compile(r"(?:不采用|不提供|不出具|不需|无需|免于|不以)[^。；;]{0,10}?保函")
# 保函只是付款前提（「待乙方提交银行保函后支付」）：合同款里仍有待付的质保金
_PAY_AFTER_GUARANTEE_RE = re.compile(r"保函[^。；;]{0,8}?(?:后|方可|才)[^。；;]{0,6}?(?:支付|付款|付清|返还)")
# 现金质保金的写法：条款里出现「质保金」字样，或预留 / 扣留 / 返还等
_RETAINED_RE = re.compile(r"质保金|保修金|预留|扣留|留存|暂扣|扣除|扣减|暂留|保留|返还|退还|无息")


def retention_is_guarantee(fields: dict) -> bool:
    """
    合同的「质保金」只是乙方另开的银行质保保函，合同款里没有扣留质保金：须正向写明乙方开具保函，
    且没有否定保函、没有「保函后付款」、没有任何现金质保金写法；拿不准就按质保金问律师
    （#43 复审：不能只凭出现「保函」二字跳过律师确认）。
    """
    clause = re.sub(r"\s+", "", _str_value(fields, "retention_clause_text") or "")
    return (
        bool(_GUARANTEE_ISSUE_RE.search(clause))
        and not _GUARANTEE_NEGATED_RE.search(clause)
        and not _PAY_AFTER_GUARANTEE_RE.search(clause)
        and not _RETAINED_RE.search(clause)
    )


# 「支付至」比例由付款节点匹配得出时的来源前缀：retention 断点据此不再问律师
PAYABLE_FROM_STAGES = "按律师规则：付款节点"


def _payment_progress(fields: dict) -> tuple[list[PaymentStage], float, float] | None:
    """付款节点 + 已付比例 + 「已付 + 欠款」比例（%）；节点解析不出或金额缺失时返回 None。"""
    total, paid, unpaid = _amount_parts(fields)
    stages = parse_payment_stages(_str_value(fields, "payment_clause_text"))
    if not stages or not total or paid is None or unpaid is None:
        return None
    return stages, paid / total * 100, (paid + unpaid) / total * 100


def _stage_label(fields: dict, no: str) -> str:
    """节点编号 → 起诉状写法：多级编号写「第7.6条」；一级编号挂在条款位置后（「第二十八章第1项」）。"""
    if "." in no or "．" in no:
        return f"第{no}条"
    loc = _str_value(fields, "payment_clause_location") or ""
    loc = re.sub(r"[（(].*$", "", loc).strip()
    return f"{loc}第{no}项" if loc.startswith("第") else f"第{no}项"


def payable_ratio_unmatched_note(fields: dict) -> str | None:
    """「已付 + 欠款」占比与付款节点都对不上时给律师的提示（校验面板与出处共用）；对得上或无从判断返回 None。"""
    progress = _payment_progress(fields)
    if progress is None:
        return None
    stages, _, due = progress
    if match_cumulative(stages, due):
        return None
    cums = "、".join(f"{s.end:g}%" for s in stages)
    return (
        f"已付 + 欠款占合同总价 {_plain_number(due)}%，与付款条款各节点的累计比例（{cums}）都对不上："
        "可能调过价、实际台数与合同不同或质保金到期时间不统一，请律师手动核对"
    )


def payable_ratio_field(fields: dict) -> dict | None:
    """
    「被告应按合同约定支付至__合同款」（确认单（五）第 3 题、律师反馈）：
    先用「已付 + 欠款」占合同总价的比例匹配付款条款的累计节点，对上即取该比例；
    都对不上交律师核对（留空）。付款条款解析不出时按质保金：无质保金 100%，有质保金由 retention 断点问律师。
    """
    progress = _payment_progress(fields)
    if progress is not None:
        stages, _, due = progress
        hit = match_cumulative(stages, due)
        if hit:
            label = _stage_label(fields, hit.no) if hit.no else "付款条款"
            return {
                "value": f"{hit.end:g}%",
                "src": f"{PAYABLE_FROM_STAGES}：已付 + 欠款占合同总价 {_plain_number(due)}%，与{label}付至 {hit.end:g}% 相符",
            }
        return {"value": None, "src": f"{payable_ratio_unmatched_note(fields)}，待核实"}
    ratio = parse_percent(_str_value(fields, "retention_ratio"))
    if ratio and retention_is_guarantee(fields):
        return {
            "value": "100%",
            "src": f"合同约定的 {ratio:g}% 为乙方开具的银行质保保函，未扣留合同款，待核实",
        }
    if ratio == 0:
        return {"value": "100%", "src": "合同无质保金"}
    if ratio is None:
        return {"value": "100%", "src": "未识别到质保金约定，待核实"}
    return {"value": "100%", "src": f"合同质保金 {ratio:g}%，请确认支付比例，待核实"}


def _owed_payment_stages(fields: dict) -> list[PaymentStage] | None:
    """欠款对应的付款节点；「已付 + 欠款」与节点对不上时不引用（返回 None，退回归纳句）。"""
    progress = _payment_progress(fields)
    if progress is None:
        return None
    stages, paid, due = progress
    if not match_cumulative(stages, due):
        return None
    return owed_stages(stages, paid, due) or None


def payment_cite_location_field(fields: dict) -> dict | None:
    """
    「依据合同___约定」：只写欠款对应的付款节点，条款号写到该项（确认单（五）第 3 题，如「第7.6条、第7.8条」）。
    匹配不上付款节点时沿用抽取的付款条款位置。
    """
    owed = _owed_payment_stages(fields)
    labels = list(dict.fromkeys(_stage_label(fields, s.no) for s in owed if s.no)) if owed else []
    # 一级编号共用条款位置前缀：「第二十八章第1项、第2项」，不重复写「第二十八章」
    prefix = re.match(r"(.*)第[^第]+项$", labels[0]).group(1) if labels and labels[0].endswith("项") else ""
    if prefix:
        labels = [labels[0]] + [lb[len(prefix):] if lb.startswith(prefix) else lb for lb in labels[1:]]
    if not labels:
        # 匹配不上，或付款条款本身没有分项编号：沿用抽取的付款条款位置
        node = _node(fields, "payment_clause_location")
        return dict(node) if node.get("value") else None
    return {
        "value": "、".join(labels),
        "src": "按律师规则：欠款对应的付款节点" + _source_risk_note(fields, "payment_clause_text", "付款条款"),
    }


_STAGE_TITLE_RE = re.compile(r"^[^，,。；;：:]{1,12}[：:]")


def payment_cite_text_field(fields: dict) -> dict | None:
    """
    「依据合同第X条约定，___。」：欠款对应的付款节点照抄原文、加引号（确认单（五）第 3 题选 A）；
    匹配不上付款节点时沿用 AI 归纳的付款条款（待核实）。
    """
    owed = _owed_payment_stages(fields)
    if not owed:
        for key in ("payment_clause_summary", "payment_clause_text"):  # 没有归纳时退回原文（确认单第 8 题）
            node = _node(fields, key)
            if node.get("value"):
                return dict(node)
        return None
    # 去掉节点开头的小标题（「结算款及质保保函：」「验收款：」），其余照抄
    texts = list(dict.fromkeys(_STAGE_TITLE_RE.sub("", s.text).rstrip("。.") for s in owed))
    return {
        "value": "“" + "；".join(texts) + "”",
        "src": "按律师规则：照抄欠款对应的付款节点原文，待核实"
        + _source_risk_note(fields, "payment_clause_text", "付款条款"),
    }


# ── 统一入口 ──────────────────────────────────────────────────────────────────
# (字段, 派生函数, 是否覆盖抽取值)。覆盖=True：规则比 LLM 抽取更可靠，派生得出就替换；
# 覆盖=False：只在缺失或上次也是派生值时填。顺序有依赖：原告名称在信息表查询之前，
# 管辖辖区在管辖句之前，台数在前。
_DERIVERS: list[tuple[str, Callable[[dict], dict | None], bool]] = [
    ("plaintiff_name_final", plaintiff_name_field, True),
    ("defendant_name", defendant_name_field, True),
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
    ("penalty_cap", penalty_cap_field, True),
    ("penalty_daily_text", penalty_daily_text_field, True),
    ("penalty_start_date", penalty_start_date_field, True),
    ("penalty_until_date", penalty_until_date_field, True),
    ("penalty_amount", penalty_amount_field, True),
    ("debt_term", debt_term_field, True),
    ("warranty_expiry_sentence", warranty_expiry_field, True),
    ("contract_total_stated", contract_total_stated_field, True),
    ("supplement_sentence", supplement_sentence_field, True),
    ("paid_note", paid_note_field, True),
    ("unpaid_note", unpaid_note_field, True),
    ("document_kind", document_kind_field, False),
    ("court_district", court_district_field, False),
    ("jurisdiction_text", jurisdiction_text_field, False),
    ("addressee", addressee_field, False),
    ("payable_ratio", payable_ratio_field, False),
    ("payment_cite_location", payment_cite_location_field, True),
    ("payment_cite_text", payment_cite_text_field, True),
    # 依赖 payable_ratio（核对 总价 × 比例 是否等于 已付 + 欠款），放在它后面
    ("payable_amount", payable_amount_field, True),
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
