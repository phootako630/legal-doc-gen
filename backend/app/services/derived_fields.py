# 派生字段：按律师确认的填写规则，由已抽取字段确定性推出起诉状里的若干取值与措辞。
#
# 规则来源：律师诉状模板的红字注释 + 《起诉状规则确认单》的答复（题号见各函数）。
#   - 原告名称：买卖合同 = 总公司全称；安装合同 = 总公司全称 + 审批表「合同分公司」中的第一个分公司（第 6 题）
#   - 原告电话固定；信用代码 / 负责人 / 住址取分公司信息表（第 7 题）
#   - 买卖合同措辞：「安装」→「供货」，「安装价格」→「产品价格」（第 18 题）
#   - 最终台数：三源不一致时以验收报告为准；审批表与合同一致而报告不同，标待核实（第 3 题）
#   - 逾期利息：合同有甲方逾期付款利率则用合同约定，否则用 LPR 常规话术（第 14 题）
#   - 管辖：按争议条款约定的地点写管辖句与致送法院（第 13 题），推定结果一律待核实
#   - 付款比例：合同有质保金且本次不起诉质保金时，「支付至 100%」改为扣除质保金比例（第 9 题）
# 律师改过的值（src 以「律师」开头）一律保留，不覆盖：AI 只提议，律师拍板。
from __future__ import annotations

import re
from collections.abc import Callable

from app.config import PLAINTIFF_HQ_NAME, PLAINTIFF_PHONE
from app.services.branch_registry import lookup_plaintiff
from app.services.validators import parse_qty

# 逾期付款利息的常规话术（诉状中"按照……计至实际付清之日止"）
LPR_INTEREST_BASIS = "全国银行间同业拆借中心公布的贷款市场报价利率"
# 律师提供的条文写法（第 13 题，待律师复核条文号）
_GENERAL_JURISDICTION_BASIS = "依据《民诉法》第34条"
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
_SITE_RE = re.compile(r"工程所在地|项目所在地|合同履行地|履行地")
_SIGN_PLACE_RE = re.compile(r"合同签订地|签订地")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


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


def derive_plaintiff_name(kind: str, branch_raw: str | None) -> tuple[str, bool] | None:
    """
    原告全称。返回 (名称, 是否需核实)；推不出返回 None。
    安装合同取「合同分公司」路径中第一个以「分公司」结尾的层级（如「集团/营销网络/江苏分公司」
    → 江苏分公司）；路径里有多个分公司时仍取第一个，但标待核实（律师尚在确认多级写法）。
    """
    if kind == "买卖":
        return PLAINTIFF_HQ_NAME, False
    if not branch_raw:
        return None
    segments = [re.sub(r"\s+", "", s) for s in re.split(r"[/／\\\\>]", branch_raw)]
    branches = [s for s in segments if s.endswith("分公司")]
    if not branches:
        return None
    name = branches[0]
    full = name if name.startswith(PLAINTIFF_HQ_NAME) else PLAINTIFF_HQ_NAME + name
    return full, len(branches) > 1


def plaintiff_name_field(fields: dict) -> dict | None:
    kind = contract_kind(fields)
    branch_raw = _str_value(fields, "plaintiff_branch_raw")
    derived = derive_plaintiff_name(kind, branch_raw)
    if derived is None:
        return None
    name, uncertain = derived
    if kind == "买卖":
        src = "按律师规则：买卖合同原告为总公司"
    else:
        src = f"按律师规则：总公司全称 + 审批表「合同分公司」（{branch_raw}）中的分公司"
        if uncertain:
            src += "；路径中有多个分公司，待核实"
    return {"value": name, "src": src, "channel": "text"}


def plaintiff_phone_field(_fields: dict) -> dict:
    return {
        "value": PLAINTIFF_PHONE,
        "src": "按律师规则：原告电话统一",
        "channel": "text",
    }


def _registry_field(attr: str) -> Callable[[dict], dict | None]:
    """从分公司信息表取原告的某一项；表里没有则不派生（保持缺失）。"""

    def derive(fields: dict) -> dict | None:
        info = lookup_plaintiff(_str_value(fields, "plaintiff_name_final"))
        value = getattr(info, attr, None) if info else None
        if not value:
            return None
        return {"value": value, "src": "分公司信息表", "channel": "text"}

    return derive


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
def elevator_qty_field(fields: dict) -> dict | None:
    """最终台数：有验收报告台数就用它；没有则不派生（保留抽取值，三源冲突仍会暂停）。"""
    acceptance = parse_qty(_str_value(fields, "elevator_qty_by_acceptance"))
    if acceptance is None:
        return None
    approval = parse_qty(_str_value(fields, "elevator_qty_by_approval"))
    contract = parse_qty(_str_value(fields, "elevator_qty_by_contract"))
    src = "按验收报告台数（律师规则：台数不一致时以验收报告为准）"
    if approval is not None and approval == contract and approval != acceptance:
        src += (
            f"；审批表与合同均为 {approval} 台，与验收报告 {acceptance} 台不同，待核实"
        )
    elif {q for q in (approval, contract) if q is not None} - {acceptance}:
        others = "、".join(
            f"{name} {q} 台"
            for name, q in (("审批表", approval), ("合同", contract))
            if q is not None and q != acceptance
        )
        src += f"（{others}）"
    return {"value": acceptance, "src": src, "channel": "text"}


# ── 逾期利息 ──────────────────────────────────────────────────────────────────
def interest_basis_field(fields: dict) -> dict:
    """合同有甲方逾期付款的利率约定就用约定（待核实措辞），否则 LPR 常规话术。"""
    breach = _str_value(fields, "breach_interest_rate_text")
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


# ── 管辖 ──────────────────────────────────────────────────────────────────────
def _jurisdiction(fields: dict) -> tuple[str, str | None, str]:
    """
    按争议条款判定管辖方式，返回 (方式, 法院辖区或法院名, 说明)。
    方式：arbitration / named / plaintiff / defendant / sign_place / site / general。
    """
    dispute = re.sub(r"\s+", "", _str_value(fields, "dispute_clause_text") or "")
    if "仲裁" in dispute:
        return "arbitration", None, "合同约定仲裁，请律师确定"
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
        site = _str_value(fields, "project_site")
        return (
            "site",
            derive_court_district(site),
            f"按工程所在地「{site or '未识别'}」推定，待核实管辖",
        )
    addr = _str_value(fields, "defendant_address")
    return (
        "general",
        derive_court_district(addr),
        "未约定具体地点，按被告住所地推定，待核实",
    )


def court_district_field(fields: dict) -> dict:
    mode, district, note = _jurisdiction(fields)
    if district is None:
        return {"value": None, "src": note}
    return {"value": district, "src": note, "channel": "text"}


def jurisdiction_text_field(fields: dict) -> dict:
    """争议条款之后的管辖句（第 13 题三种写法 + 工程所在地的模板原句）。"""
    mode, _, note = _jurisdiction(fields)
    court = _str_value(fields, "court_district") or _MISSING
    if mode == "arbitration":
        return {"value": None, "src": note}
    if mode == "site":
        site = _str_value(fields, "project_site") or _MISSING
        text = f"因工程所在地为{site}，属{court}法院辖区，故原告向{court}人民法院提起诉讼。"
    elif mode == "general":
        text = f"{_GENERAL_JURISDICTION_BASIS}，故原告向{court}人民法院提起诉讼。"
    else:
        text = f"故原告向{court}人民法院提起诉讼。"
    return {
        "value": text,
        "src": note + "（管辖句由系统按律师规则生成，待核实）",
        "channel": "text",
    }


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


def payable_ratio_field(fields: dict) -> dict:
    """「被告应按合同约定支付至__合同款」：默认 100%；有质保金且本次不起诉质保金则扣除。"""
    ratio = parse_percent(_str_value(fields, "retention_ratio"))
    claim = _str_value(fields, "claim_includes_retention")
    if ratio == 0:
        return {"value": "100%", "src": "合同无质保金"}
    if ratio is None:
        return {"value": "100%", "src": "未识别到质保金约定，待核实"}
    pct = f"{ratio:g}%"
    if claim == "是":
        return {"value": "100%", "src": f"合同质保金 {pct}，本次起诉包含质保金"}
    if claim == "否":
        return {
            "value": f"{100 - ratio:g}%",
            "src": f"合同质保金 {pct}，本次起诉不含质保金",
        }
    return {
        "value": "100%",
        "src": f"合同质保金 {pct}，请确认本次是否起诉质保金，待核实",
    }


# ── 统一入口 ──────────────────────────────────────────────────────────────────
# (字段, 派生函数, 是否覆盖抽取值)。覆盖=True：规则比 LLM 抽取更可靠，派生得出就替换；
# 覆盖=False：只在缺失或上次也是派生值时填。顺序有依赖：原告名称在信息表查询之前，
# 管辖辖区在管辖句之前，台数在前。
_DERIVERS: list[tuple[str, Callable[[dict], dict | None], bool]] = [
    ("plaintiff_name_final", plaintiff_name_field, True),
    ("plaintiff_phone", plaintiff_phone_field, True),
    ("plaintiff_credit_code", _registry_field("credit_code"), True),
    ("plaintiff_person_in_charge", _registry_field("person_in_charge"), True),
    ("plaintiff_address", _registry_field("address"), True),
    ("contract_action", contract_action_field, True),
    ("price_term", price_term_field, True),
    ("elevator_qty", elevator_qty_field, True),
    ("interest_rate_basis", interest_basis_field, False),
    ("court_district", court_district_field, False),
    ("jurisdiction_text", jurisdiction_text_field, False),
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
    return fields
