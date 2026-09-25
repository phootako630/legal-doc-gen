# 派生字段：由已抽取字段按律师惯用话术/规则确定性推出（逾期利息计算标准、管辖法院辖区）。
#
# 设计意图（对应律师诉状模板的红字注释）：
#   - 逾期利息："如果合同没有具体约定，这个就是常规话术"——默认填 LPR 常规话术；
#     合同另有违约利率约定时仍给常规话术，但标「待核实」并附合同原文，由律师决定是否改用。
#   - 管辖法院：诉状写"因工程所在地为X，属Y法院辖区，故原告向Y人民法院提起诉讼"，
#     Y 按工程所在地的「市+区/县」推定；合同约定仲裁则不推定。推定结果一律标「待核实」。
#   律师已填/已改的值一律保留，不覆盖（AI 只提议，律师拍板）。
from __future__ import annotations

import re

# 逾期付款利息的常规话术（诉状中"按照……计至实际付清之日止"）
LPR_INTEREST_BASIS = "全国银行间同业拆借中心公布的贷款市场报价利率"

_CJK = "\\u4e00-\\u9fa5"  # 常用汉字区间（正则转义形式）
# 地址前常见的方位词（「位于南京市…」），不去掉会被误当成市名的一部分
_LEADING_WORDS_RE = re.compile(r"^(?:项目|工程)?(?:位于|坐落于|地处|地址为|地址：|在)")
_PROVINCE_RE = re.compile(f"^[{_CJK}]{{2,7}}?(?:省|自治区)")
# 市 + 区/县/县级市/旗；从串首匹配，匹配不上宁可不推定（不猜）
_CITY_DISTRICT_RE = re.compile(
    f"^([{_CJK}]{{2,6}}?市)([{_CJK}]{{1,6}}?(?:新区|区|县|市|旗))"
)


def _str_value(fields: dict, key: str) -> str | None:
    node = fields.get(key)
    val = node.get("value") if isinstance(node, dict) else node
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def derive_court_district(project_site: str | None) -> str | None:
    """
    由工程所在地推定管辖法院辖区（「市+区/县」），如
    「江苏省南京市雨花台区某某二期项目」→「南京市雨花台区」。推不出返回 None。
    """
    if not project_site:
        return None
    s = re.sub(r"\s+", "", project_site)
    s = _LEADING_WORDS_RE.sub("", s, count=1)
    s = _PROVINCE_RE.sub("", s, count=1)
    m = _CITY_DISTRICT_RE.match(s)
    if not m:
        return None
    return m.group(1) + m.group(2)


def interest_basis_field(fields: dict) -> dict:
    """逾期利息计算标准：默认常规话术；合同另有违约利率约定则标待核实并附原文。"""
    breach = _str_value(fields, "breach_interest_rate_text")
    if breach:
        src = f"常规话术；合同另有违约利率约定「{breach}」，待核实是否改用合同约定"
    else:
        src = "常规话术（合同未另行约定逾期利率）"
    return {"value": LPR_INTEREST_BASIS, "src": src, "channel": "text"}


def court_district_field(fields: dict) -> dict:
    """管辖法院辖区：按工程所在地推定；约定仲裁或推不出则置空交律师。"""
    dispute = _str_value(fields, "dispute_clause_text") or ""
    if "仲裁" in dispute:
        return {"value": None, "src": "争议解决条款涉及仲裁，请律师确定管辖"}
    site = _str_value(fields, "project_site")
    district = derive_court_district(site)
    if district is None:
        return {"value": None, "src": ""}
    return {
        "value": district,
        "src": f"按工程所在地「{site}」推定，待核实管辖",
        "channel": "text",
    }


def _should_derive(node: object) -> bool:
    """空值 → 推；上次由本模块推出且未经律师改动 → 重推（依赖字段可能已被律师改过）。"""
    if not isinstance(node, dict):
        return node is None or str(node).strip() == ""
    value = node.get("value")
    if value is None or str(value).strip() == "":
        return True
    # 律师修改路径（审核页编辑 / 断点决定）都会把 src 改写为「律师…」，此时保留律师的值
    return bool(node.get("derived")) and not str(node.get("src") or "").startswith(
        "律师"
    )


def apply_derived_fields(fields: dict) -> dict:
    """就地补齐/刷新派生字段（律师修改过的值不覆盖），返回 fields 以便链式调用。"""
    derivers = {
        "interest_rate_basis": interest_basis_field,
        "court_district": court_district_field,
    }
    for key, derive in derivers.items():
        if _should_derive(fields.get(key)):
            fields[key] = {**derive(fields), "derived": True}
    return fields
