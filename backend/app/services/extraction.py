# 抽取流程的框架无关纯 helper：拼装文本 / OCR 标记 / 校验结论渲染
#
# 供 v1 的 routers/extract.py 与 v2 的 agent/nodes.py 共用，避免重复实现。
from __future__ import annotations

import re
from typing import Any

from app.services.acceptance_report import latest_approved_date
from app.services.confidence import ConfidenceSignals, compute_confidence
from app.services.provenance import (
    _src_filename,
    replace_src_page,
    resolve_provenance,
    src_page,
)
from app.services.validators import ValidationCheck, parse_amount, parse_date


def build_combined_text(files: list[dict]) -> str:
    """拼装带文件名/类型/OCR标记的文本块，给模型结构化的出处依据。

    每个 file 需含 filename / identified_type / is_scanned / text 字段。
    """
    blocks: list[str] = []
    for f in files:
        name = f.get("filename", "未知文件")
        dtype = f.get("identified_type", "未知")
        body = f.get("text") or ""
        if f.get("is_scanned"):
            if body.strip():
                tag = "（本文件为 OCR 扫描识别，文字可能存在误差）"
            else:
                # 扫描件尚未 OCR（按需 OCR 延后到 agent 阶段）：给占位文案，
                # 让清点/抽取知道该文件存在但暂无正文，不要误判为空文件
                tag = "（扫描件，暂未 OCR，正文将在需要时按需识别）"
                body = "[扫描件正文暂未识别]"
        else:
            tag = ""
        blocks.append(f"【文件：{name}｜{dtype}】{tag}\n{body}")
    return "\n\n---\n\n".join(blocks)


def mark_ocr_fields(data: Any, scanned_filenames: set[str]) -> None:
    """
    递归遍历 extracted_fields，若某字段的 src 引用了扫描件文件名，
    代码层面确保 src 中带有 OCR 标记——不依赖模型是否记得主动声明，
    因为前端/渲染是靠 src 文本里的"OCR"/"扫描"关键词判定待核实状态的。
    """
    if not scanned_filenames:
        return
    if isinstance(data, dict):
        if "value" in data and "src" in data and isinstance(data.get("src"), str):
            src = data["src"]
            if src and "OCR" not in src and any(fn in src for fn in scanned_filenames):
                data["src"] = f"{src}（OCR识别，请核实）"
            return
        for v in data.values():
            mark_ocr_fields(v, scanned_filenames)
    elif isinstance(data, list):
        for item in data:
            mark_ocr_fields(item, scanned_filenames)


# 验收报告里的设备代码（每台电梯唯一）与报告编号（每台一份报告）
_EQUIPMENT_CODE_RE = re.compile(r"设备代码\s*[:：]?\s*([0-9A-Za-z]{10,})")
# 编号里可能带括号，如「WJ-TJ(3110)-2023-01211」（检验机构代号）；不认括号会一份都数不出来
_REPORT_NO_RE = re.compile(r"报告编号\s*[:：]\s*([0-9A-Za-z\-()（）]{6,})")


def count_acceptance_units(files: list[dict]) -> tuple[int, str] | None:
    """
    数验收报告覆盖的电梯台数（确定性代码，对应 CLAUDE.md「验收口径 = 报告份数/设备代码数」）。
    优先按不同设备代码计数，取不到再按不同报告编号计数；都取不到返回 None。
    返回 (台数, 出处说明)。
    """
    reports = [
        f for f in files if f.get("identified_type") == "验收报告" and f.get("text")
    ]
    if not reports:
        return None
    names = "、".join(f"《{f.get('filename', '验收报告')}》" for f in reports)
    text = "\n".join(f["text"] for f in reports)
    for pattern, label in (
        (_EQUIPMENT_CODE_RE, "设备代码"),
        (_REPORT_NO_RE, "报告编号"),
    ):
        found = set(pattern.findall(text))
        if found:
            return len(
                found
            ), f"{names}按{label}计数：共 {len(found)} 个不同{label}（代码计数）"
    return None


def mark_supplement_agreements(fields: dict, files: list[dict]) -> None:
    """
    上传材料里有补充协议时记下文件名（确认单（五）第 8 题）：起诉状据此加一句
    「后双方签订补充协议，确认合同总价变更为…」，并提醒律师核对。补充协议本身不 OCR。
    """
    names = [f.get("filename", "") for f in files if f.get("identified_type") == "补充协议"]
    if names:
        fields["supplement_agreement"] = {
            "value": "、".join(f"《{n}》" for n in names),
            "src": "上传材料中识别到补充协议（按文件名 / 标题），请律师核对其内容",
        }


# 旧版 KA 审批表（合同类型「买卖附带安装合同」）一张表同时列买卖、安装两份合同的金额（律师新案例：徐州九里苏宁）
_AMT = r"\*?([\d,]+(?:\.\d+)?)"
_DUAL_SECTION_RE = {
    "买卖": re.compile(f"买卖合同总额{_AMT}已付款金额{_AMT}未付款金额{_AMT}(.*?)(?=安装合同总额|其他情况|$)"),
    "安装": re.compile(f"安装合同总额{_AMT}已付款金额{_AMT}未付款金额{_AMT}(.*?)(?=买卖合同总额|其他情况|$)"),
}
_SECTION_RETENTION_RE = re.compile(r"质保金([\d,]+(?:\.\d+)?)元")
_SALE_TITLE_RE = re.compile(r"供货|买卖|设备|采购|购销")


def _case_contract_kind(files: list[dict]) -> str | None:
    """
    本案合同是买卖还是安装：文件名与正文标题一起看，只出现一类字样才判；两类都有（「电梯设备安装合同」
    「设计、供应及安装」）或都没有、合同不止一份时返回 None，不动审批表金额（#80 评审）。
    """
    contracts = [f for f in files if f.get("identified_type") == "合同"]
    if len(contracts) != 1:
        return None
    f = contracts[0]
    title = f"{f.get('filename', '')}{(f.get('text') or '')[:60]}"
    sale, install = bool(_SALE_TITLE_RE.search(title)), "安装" in title
    if sale == install:
        return None
    return "买卖" if sale else "安装"


def apply_dual_contract_amounts(fields: dict, files: list[dict]) -> None:
    """
    审批表同时列了买卖、安装两份合同时，按本案合同取对应一栏的合同总额 / 已付 / 未付 / 未付质保金，
    合同类型改为本案的那一种（LLM 会把两栏加总，或都取买卖栏）。判断不了本案是哪份合同时不动，留给律师。
    """
    approval = next(
        (f for f in files if f.get("identified_type") == "审批表"
         and "买卖合同总额" in re.sub(r"\s+", "", f.get("text") or "")
         and "安装合同总额" in re.sub(r"\s+", "", f.get("text") or "")),
        None,
    )
    kind = _case_contract_kind(files) if approval else None
    if not approval or not kind:
        return
    m = _DUAL_SECTION_RE[kind].search(re.sub(r"\s+", "", approval["text"]))
    if not m:
        return
    name = approval.get("filename", "审批表")
    # 扫描审批表 OCR 出来的金额仍须带待核实标记：审核页与起诉状按 src 判断状态（#80 评审）
    ocr = "（OCR识别，请核实）" if approval.get("is_scanned") else ""
    channel = {"channel": "ocr"} if ocr else {}
    note = f"《{name}》{kind}合同栏（审批表同时列买卖、安装两份合同，按本案{kind}合同取）"
    for key, raw in zip(("total_amount", "paid_amount", "unpaid_amount"), m.groups()[:3]):
        value = parse_amount(raw)
        if value is not None:
            fields[key] = {"value": value, "src": note + ocr, **channel}
    retention = _SECTION_RETENTION_RE.search(m.group(4))
    amount = parse_amount(retention.group(1)) if retention else None
    if amount is not None:
        fields["retention_unpaid_amount"] = {
            "value": amount, "src": f"{note}未付款项构成-质保金{ocr}", **channel
        }
    else:
        # 本栏没写质保金：不能沿用另一栏的值，也不凭空记 0，交律师核实（#80 评审）
        fields["retention_unpaid_amount"] = {
            "value": None, "src": f"{note}未列质保金，请律师核实"
        }
    fields["contract_type"] = {
        "value": f"{kind}合同",
        "src": f"《{name}》合同类型「买卖附带安装合同」，本案为{kind}合同{ocr}",
        **channel,
    }


def apply_acceptance_count(fields: dict, files: list[dict]) -> None:
    """用代码计数覆盖 LLM 给的验收口径台数；数不出来则保留 LLM 的值。"""
    counted = count_acceptance_units(files)
    if counted is None:
        return
    qty, src = counted
    fields["elevator_qty_by_acceptance"] = {"value": qty, "src": src}


def apply_acceptance_date(fields: dict, files: list[dict]) -> None:
    """
    文字版验收报告的最晚批准日期由代码在**全部**报告文件里取（律师规则：多份取最晚一批）：一个案件常是
    每台电梯一份 PDF，LLM 容易只看第一份。读不到「批准」日期时保留 LLM 的值；扫描报告由
    report_ocr 节点按同一规则合并。
    """
    best: tuple[tuple[int, int, int], str, str, int | None] | None = None
    for f in files:
        # 部分扫描的报告（is_scanned 但有文字层）也用文字页取日期；纯扫描报告此时还没有 text（#79 评审）
        if f.get("identified_type") != "验收报告" or not f.get("text"):
            continue
        pages = f.get("pages") or [{"page": None, "text": f["text"]}]
        found = latest_approved_date(pages)
        ymd = parse_date(found[0]) if found else None
        if found and ymd and (best is None or ymd > best[0]):
            best = (ymd, found[0], f.get("filename", "验收报告"), found[1])
    if best is None:
        return
    _, value, filename, page = best
    where = f"第{page}页" if page else ""
    fields["acceptance_latest_date"] = {
        "value": value,
        "src": f"《{filename}》{where}批准日期（代码取全部验收报告中最晚）",
    }


# 律师规则（确认单第 5 题）：被告住址、法定代表人一律用工商登记信息，审批表上的地址不用
_REGISTRY_ONLY_KEYS = ("defendant_address", "defendant_legal_rep")
_REGISTRY_ONLY_NOTE = "按律师规则：被告住址、法定代表人用工商登记信息（企查查、爱企查、启信宝等），请查询后填写"
_SUMMARY_MARK = "AI 归纳，待核实"


def _has_value(fields: dict, key: str) -> bool:
    node = fields.get(key)
    return isinstance(node, dict) and node.get("value") not in (None, "")


# 合同编号（#125）：审批表标题紧挨合同号栏时，模型会把「AH1234567-某某项目」整段当合同号。
# 编号本身是「字母 + 数字」，后面跟连字符 / 空格再接中文的，只保留编号；
# 含中文又切不出编号的不猜，原样保留并标待核实。
_CONTRACT_NO_TAIL_RE = re.compile(r"^\s*([A-Za-z]{1,6}\d{4,})\s*[-－—–_\s]+\s*[\u4e00-\u9fff]")
_CONTRACT_NO_TRIM_NOTE = "（合同号后所带项目名已去掉）"
_CONTRACT_NO_ODD_NOTE = "（合同编号格式不常见，待核实）"
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_RETENTION_RETURN_RE = re.compile(r"返还[^。；;]{0,6}(保修|质保)|(保修|质保)(款|金)[^。；;]{0,12}返还")
# 独立的「逾期支付合同价款」义务：动词是支付 / 付款，对象是价款。只出现价款名称不算——
# 「未按时返还安装费质保金」里的「安装费」只是质保金的前缀（#53 复审）
_PRICE = r"(?:合同价款|合同款|工程款|安装费|货款|进度款|价款)"
_CONTRACT_PRICE_RE = re.compile(
    rf"(?:逾期|迟延|延期|延迟|拖欠|未及时|未按[^。；;，,]{{0,6}}?)(?:支付|付款|付清)[^。；;，,]{{0,8}}{_PRICE}"
    rf"|(?:支付|付款)[^。；;，,]{{0,4}}{_PRICE}[^。；;，,]{{0,6}}(?:逾期|延期|迟延)"
    rf"|(?:拖欠|欠付){_PRICE}"
)


# 主合同只写「争议的解决……按战略合同内相关条款执行」，本身没有约定仲裁 / 法院（律师反馈：徐州苏宁）
_STRATEGIC_REF_RE = re.compile(r"(?:战略|框架)(?:合同|协议|合作)")
_FORUM_RE = re.compile(r"仲裁|法院|诉讼|管辖")
# 战略 / 框架合同只能提供这些条款：它不是本案合同（与 agent/nodes.py 的按需 OCR 白名单一致）
STRATEGIC_CLAUSE_KEYS = frozenset({
    "payment_clause_location",
    "payment_clause_text",
    "payment_clause_summary",
    "breach_interest_clause_location",
    "breach_interest_rate_text",
    "breach_clause_text",
    "dispute_clause_location",
    "dispute_clause_text",
    "arbitration_institution",
    "warranty_period",
    "retention_ratio",
    "retention_clause_text",
})
STRATEGIC_LABEL = "战略合同"


def refers_to_strategic(text: str | None) -> bool:
    """争议条款只是转引战略 / 框架合同，自己没写仲裁机构或法院。"""
    compact = re.sub(r"\s+", "", text or "")
    return bool(_STRATEGIC_REF_RE.search(compact)) and not _FORUM_RE.search(compact)


def _from_files(node: object, names: list[str]) -> bool:
    src = str(node.get("src") or "") if isinstance(node, dict) else ""
    return any(name and name in src for name in names)


def apply_strategic_guards(fields: dict, files: list[dict]) -> None:
    """
    战略 / 框架合同的把关（确定性代码）：
    - 取自战略合同的当事人、签约日期、合同名称、台数、地点、金额等本案字段一律清空（它不是本案合同）；
    - 争议条款取自战略合同时，位置写成「战略合同第X条」，起诉状不会误写成「案涉合同第X条」；
    - 主合同的争议条款只是转引战略合同（没写仲裁 / 法院）时清空条款内容、保留主合同条款位置，
      交给按需读取去战略合同里找（律师反馈：徐州苏宁）。
    """
    names = [f.get("filename", "") for f in files if f.get("identified_type") == "战略合同"]
    if not names:
        return
    for key, node in list(fields.items()):
        if key not in STRATEGIC_CLAUSE_KEYS and _from_files(node, names):
            fields[key] = {"value": None, "src": "按律师规则：战略 / 框架合同不是本案合同，此项不取自战略合同"}
    text = fields.get("dispute_clause_text")
    loc = fields.get("dispute_clause_location")
    if _from_files(text, names) and isinstance(loc, dict) and loc.get("value"):
        if STRATEGIC_LABEL not in str(loc["value"]) and _from_files(loc, names):
            loc["value"] = f"{STRATEGIC_LABEL}{loc['value']}"
    elif isinstance(text, dict) and refers_to_strategic(text.get("value")):
        fields["dispute_clause_text"] = {
            "value": None,
            "src": f"主合同约定争议解决按战略 / 框架合同执行（原文：{text.get('value')}），待从战略合同中读取",
            "strategic_ref": True,
        }


def apply_rule_guards(fields: dict) -> None:
    """
    抽取后按律师规则把关（确定性代码，不依赖模型是否照做）：
    - 被告住址 / 法定代表人若取自审批表 → 清空，提示律师查工商登记信息；
    - 付款条款的 AI 归纳句不是合同原文 → src 标「AI 归纳，待核实」，审核页与起诉状都会提示。
    """
    for key in _REGISTRY_ONLY_KEYS:
        node = fields.get(key)
        if (
            isinstance(node, dict)
            and node.get("value") not in (None, "")
            and "审批表" in str(node.get("src") or "")
        ):
            # 审批表地址不写进起诉状，但留作推断被告所在地法院的参考（新案卷核对）
            if key == "defendant_address" and not _has_value(fields, "defendant_address_ref"):
                fields["defendant_address_ref"] = dict(node)
            fields[key] = {"value": None, "src": _REGISTRY_ONLY_NOTE}
    # 甲方逾期付款违约条款只算逾期支付合同价款的：逾期返还保修款 / 质保金的利息条款不算
    # （南京裕骁一案曾把保修协议「未按时返还保修款……支付存款利息」当成逾期付款利率）
    clause = str((fields.get("breach_clause_text") or {}).get("value") or "")
    if _RETENTION_RETURN_RE.search(clause) and not _CONTRACT_PRICE_RE.search(clause):
        for key in ("breach_clause_text", "breach_interest_rate_text", "breach_interest_clause_location"):
            fields[key] = {"value": None, "src": "按律师规则：该条款是逾期返还保修款 / 质保金的约定，不是逾期支付合同价款的违约条款"}
    _guard_contract_no(fields)
    # 合同当事人只能取自合同：曾把验收报告上的「施工单位」「使用单位」当成合同乙方 / 甲方（律师反馈）
    for key in ("contract_party_a", "contract_party_b"):
        node = fields.get(key)
        if isinstance(node, dict) and re.search(r"报告|检验|审批|诉讼流程", str(node.get("src") or "")):
            fields[key] = {"value": None, "src": "按律师规则：合同当事人只取合同盖章页，验收报告 / 审批表上的单位不算"}
    # 验收日期取不到报告批准日期、退回审批表「政府验收时间」时：不是同一个日期口径，标待核实
    node = fields.get("acceptance_latest_date")
    if isinstance(node, dict) and node.get("value"):
        src = str(node.get("src") or "")
        if "政府验收时间" in src and "待核实" not in src:
            node["src"] = f"{src}（审批表政府验收时间，非报告批准日期，待核实）"
    node = fields.get("payment_clause_summary")
    if isinstance(node, dict) and node.get("value"):
        src = str(node.get("src") or "")
        if _SUMMARY_MARK not in src:
            node["src"] = f"{src}（{_SUMMARY_MARK}）" if src else f"（{_SUMMARY_MARK}）"


def _guard_contract_no(fields: dict) -> None:
    """合同编号只保留编号本身（#125）；见 _CONTRACT_NO_TAIL_RE 的说明。"""
    node = fields.get("contract_no")
    if not isinstance(node, dict) or not isinstance(node.get("value"), str):
        return
    value, src = node["value"], str(node.get("src") or "")
    m = _CONTRACT_NO_TAIL_RE.match(value)
    if m:
        node["value"] = m.group(1)
        if _CONTRACT_NO_TRIM_NOTE not in src:
            node["src"] = f"{src}{_CONTRACT_NO_TRIM_NOTE}"
    elif _HAS_CJK_RE.search(value) and _CONTRACT_NO_ODD_NOTE not in src:
        node["src"] = f"{src}{_CONTRACT_NO_ODD_NOTE}"


# 原文里找不到的抽取值：保留但标「待核实」（审核页与起诉状都按 src 里的「待核实」判定状态）
UNANCHORED_NOTE = "（原文未找到，待核实）"
# 不是从原文摘抄的字段（AI 归纳 / 系统状态），找不到原文是正常的，不标
_NOT_ANCHORED_KEYS = {"payment_clause_summary", "internet_lookup_status"}


def _with_verified_source(src: str, prov) -> str:
    """
    让 src 与实际命中的出处一致，并按是否命中加 / 去「原文未找到」标记。
    每次重算：抽取后与 OCR 补读后各锚定一次，第二次可能才在 OCR 文本里找到原值。
    """
    src = src.replace(UNANCHORED_NOTE, "")
    if prov.filename is None:
        return f"{src}{UNANCHORED_NOTE}"
    cited = _src_filename(src)
    if cited is not None and cited != prov.filename:
        # 模型写的是 A 文件，实际在 B 文件命中：来源改成 B，避免出现「审批表第 2 页」这种
        # 文件名与页码对不上的出处。原位置描述属于 A 文件，一并去掉
        src = f"《{prov.filename}》"
        if prov.channel == "ocr":
            src += "（OCR识别，请核实）"
    elif cited is None:
        # 模型没写文件名（如「合同签订页」）：补上实际命中的文件，出处可追溯，
        # 战略合同把关等按文件判断的规则也能认出来源（#56 复审）
        src = f"《{prov.filename}》{src}"
    written = src_page(src)
    if prov.page is not None and written is not None and written != prov.page:
        # 模型写的那一页没有这个值（值在那一页能命中时锚定会优先取它）：页码改成实际命中页，
        # 审核页出处文字与「原件第 N 页」按钮一致（#120）
        src = replace_src_page(src, prov.page)
    return src


def enrich_provenance(data: Any, files: list[dict], key: str | None = None) -> None:
    """
    递归遍历 extracted_fields，为每个 FieldValue 补充**已验证**的出处信息：
      page    —— 值回原文逐页锚定得到的真实页码（未命中为 None，不伪造）
      anchor  —— 命中片段（供前端高亮/反幻觉比对）
      channel —— 命中所在文件的来源通道（扫描件 ocr / 文本 text）
      confidence —— 由确定性软信号加权得出的 0-100 分（仅供 UI 排序/默认展开）
      src     —— 改为实际命中的文件（模型没写文件名时补上）；原文找不到时追加「原文未找到，待核实」

    值保守保留（不因未命中而硬删，避免误删有效值），但未命中一定标「待核实」：
    审核页与起诉状据此提醒律师，不会把模型编造的值当作「正常」写进文书。
    """
    if isinstance(data, dict):
        if "value" in data and "src" in data:
            prov = resolve_provenance(data.get("value"), data.get("src"), files)
            data["page"] = prov.page
            data["anchor"] = prov.matched_text
            data["channel"] = prov.channel
            data["confidence"] = compute_confidence(
                ConfidenceSignals(
                    channel=prov.channel, anchor_quality=prov.anchor_quality
                )
            )
            value = data.get("value")
            has_value = value is not None and not (
                isinstance(value, str) and value.strip() == ""
            )
            if has_value and key not in _NOT_ANCHORED_KEYS:
                data["src"] = _with_verified_source(str(data.get("src") or ""), prov)
            return
        for k, v in data.items():
            enrich_provenance(v, files, k)
    elif isinstance(data, list):
        for item in data:
            enrich_provenance(item, files, key)


def format_checks_for_llm(checks: list[ValidationCheck]) -> str:
    """把确定性校验结论渲染成给 LLM 的只读事实清单——LLM 据此措辞，不得重新判断。"""
    lines: list[str] = []
    for c in checks:
        if not c.applicable:
            verdict = "未校验（信息不足）"
        elif c.passed:
            verdict = "通过"
        else:
            verdict = "冲突/不通过"
        lines.append(f"- [{verdict}] {c.message}")
    return "\n".join(lines)
