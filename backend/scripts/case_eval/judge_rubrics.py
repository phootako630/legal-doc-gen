# LLM 裁判的评分标准（#90 第 1 步）：哪些字段可以交给裁判、每个字段按什么标准判「一致」。
#
# 为什么要有裁判：规则拼出来的整句（管辖句、违约条款引用句、移交句……）与律师写法措辞稍有不同，
# 逐字比对就判错，只能人工翻报告。裁判只回答「这两句话在法律意义上是不是一回事」。
#
# 边界（比裁判本身更重要）：
#   - 只用于规则拼接的句子（JUDGE_CRITERIA 里的字段）。主体名称、合同编号、金额、日期、条款原文等
#     证据原文类字段（VERBATIM_FIELDS）必须逐字一致，配成 judge 也不用裁判，退回逐字比对并警告。
#   - 标准写清「什么算实质差异」：数字、日期、条款号、主体、法院 / 仲裁机构不同都算不一致；
#     措辞、句式、标点、系统的「⚠️ 待核实」等标注不算。
#   - 裁判结论单独列出，校准前不计入准确率（第 3、4 步）。
from __future__ import annotations

# 对所有字段都适用的判断原则（第 2 步写进裁判的提示词）
JUDGE_PRINCIPLES = (
    "你是电梯合同纠纷起诉状的核对员，判断「系统句」与「律师句」在法律意义上是否一致。",
    "数字、金额、比例、日期、条款号、当事人、法院或仲裁机构名称，只要有一处不同就是不一致。",
    "措辞、句式、语序、标点、全半角不同，不算不一致；系统句里的「⚠️ 待核实」「【…待核实】」等提示语忽略。",
    "一方有、另一方没有的实质内容（如律师写了管辖依据、系统没写），算不一致。",
    "信息不足以判断时，回答「无法判断」，不要猜。",
)

# 可以交给裁判的字段 → 默认判断标准（expected.json 可用 criteria 覆盖）
JUDGE_CRITERIA: dict[str, str] = {
    "jurisdiction_text": (
        "文书类型（向法院起诉 / 向仲裁委员会申请仲裁）相同，法院或仲裁机构是同一家；"
        "律师写了管辖依据（合同约定的条款号、民事诉讼法条文）时，系统句的依据须相同。"
    ),
    "breach_clause_sentence": (
        "引用的是同一条款（条款号相同），且都是甲方逾期付款的那一句：违约金或利息的计算方式、"
        "比例、基数、上限相同。节选范围可以不同，但不能缺少计算方式或比例。"
    ),
    "payment_cite_text": (
        "引用的是欠款对应的同一个付款节点：付款比例、期限、前置条件（验收、移交、结算等）相同。"
        "可以多引或少引不欠款的节点，但欠款节点的条件不能缺。"
    ),
    "handover_text": "电梯是否已全部移交、移交给谁（物业 / 被告 / 甲方）相同。",
    "interest_rate_basis": (
        "利率基准相同（如一年期贷款市场报价利率 LPR、同期同档次贷款利率、固定日利率），"
        "倍数或上浮比例相同；只是表述详略不同不算差异。"
    ),
    "warranty_expiry_sentence": "质保期到期日相同；一方写了到期句、另一方没写，算不一致。",
    "supplement_sentence": "都写了签订补充协议，变更后的合同总价相同。",
}

# 证据原文类字段：主体、编号、金额、日期、条款原文与位置、地址（CLAUDE.md「证据原文保留」），只能逐字比对
VERBATIM_FIELDS = frozenset({
    "plaintiff_name_final", "plaintiff_credit_code", "plaintiff_person_in_charge", "plaintiff_address",
    "defendant_name", "defendant_credit_code", "defendant_legal_rep", "defendant_address",
    "contract_no", "contract_title", "contract_party_a", "contract_party_b", "contract_sign_date",
    "total_amount", "paid_amount", "unpaid_amount", "claim_amount", "payable_amount",
    "retention_unpaid_amount", "contract_total_stated", "contract_total_in_contract",
    "contract_equipment_amount", "contract_install_amount", "penalty_amount",
    "acceptance_latest_date", "penalty_start_date", "penalty_until_date",
    "payment_clause_text", "breach_clause_text", "dispute_clause_text", "retention_clause_text",
    "handover_clause_text", "payment_clause_location", "breach_interest_clause_location",
    "dispute_clause_location", "payment_cite_location", "warranty_period",
    "project_site", "delivery_place", "install_address", "contract_sign_place",
    "arbitration_institution", "court_district", "addressee",
})


def judge_criteria(key: str, override: str | None = None) -> str:
    """字段的判断标准：expected.json 写了 criteria 用它，否则用默认标准；没有标准的字段不能交给裁判。"""
    if override and override.strip():
        return override.strip()
    if key in JUDGE_CRITERIA:
        return JUDGE_CRITERIA[key]
    raise ValueError(f"{key} 没有默认的判断标准：配成 judge 时请在 expected.json 写 criteria")
