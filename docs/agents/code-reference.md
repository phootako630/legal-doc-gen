# 代码结构与类型参考

需要项目目录、前端共享类型、字段中英名、后端配置项或 prompt 加载位置时读本文件。以下各节是代码的镜像，与代码不一致时以代码为准（`frontend/src/lib/types.ts`、`frontend/src/lib/field-map.ts`、`backend/app/config.py`、`backend/.env.example`、`prompts/`）。

## 项目结构

```
legal-doc-app/
├── CLAUDE.md
├── .claude/                         # Claude Code 辅助：启动脚本 + skills（见 local-dev.md）
├── prompts/                         # LLM Prompt 模板（纯文本）
│   ├── prompt-a-extract.md          # 字段抽取（JSON，结构化输出）
│   ├── prompt-a-validate.md         # 仅生成高亮说明文本（不再做一致性判断）
│   ├── prompt-b-generate.md         # v1 遗留，代码不再加载
│   └── complaint-template.md        # 起诉状模板常量（占位符）
│
├── frontend/                        # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/                  # shadcn/ui（自动生成，不手改）
│   │   │   ├── layout/              # Header, Container
│   │   │   ├── upload/              # FileDropzone, FileList
│   │   │   ├── processing/          # StepProgress（消费 SSE 进展）
│   │   │   ├── review/              # FieldTable, FieldRow, ValidationReport, HighlightList, ConflictResolver
│   │   │   ├── preview/             # ComplaintPreview
│   │   │   ├── branches/            # 原告信息表页面（查看 / 管理员上传 / 历史回退）
│   │   │   └── auth/                # 登录页、修改密码弹窗、页头账号菜单
│   │   ├── hooks/
│   │   │   ├── useFileUpload.ts
│   │   │   └── useComplaintFlow.ts  # 流程状态 + analyze/resume 往返
│   │   ├── lib/
│   │   │   ├── api.ts               # 后端 API 客户端（含 stream/resume）
│   │   │   ├── html-doc-generator.ts # 起诉状全文 → Word 兼容 HTML（.doc）
│   │   │   ├── field-map.ts         # 字段名英中映射
│   │   │   ├── types.ts             # 共享类型定义
│   │   │   └── utils.ts
│   │   ├── App.tsx / main.tsx / index.css
│   │   └── ...
│   └── package.json / tsconfig.json / vite.config.ts / tailwind.config.ts
│
├── backend/                         # Python FastAPI 后端
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口 + CORS
│   │   ├── routers/
│   │   │   ├── files.py             # POST /api/upload
│   │   │   ├── analyze.py           # POST /api/analyze（启动 agent，SSE）
│   │   │   ├── resume.py            # POST /api/resume（断点恢复）
│   │   │   ├── source_page.py       # POST /api/case/{run_id}/page（审核页查看扫描件原件页）
│   │   │   ├── generate.py          # POST /api/generate
│   │   │   ├── branches.py          # /api/branches（原告信息表，更新须管理员口令）
│   │   │   └── auth.py              # /api/auth（登录 / 退出 / 改密码）+ 登录校验中间件
│   │   ├── agent/
│   │   │   ├── graph.py             # LangGraph 状态图定义（节点/边/interrupt）
│   │   │   ├── state.py             # CaseState（Pydantic）
│   │   │   ├── checkpoint.py        # SQLite checkpoint 连接、会话登记、过期清理
│   │   │   └── nodes.py             # 清点/抽取/校验/生成节点
│   │   ├── services/                # 工具层（框架无关，纯函数，可单测）
│   │   │   ├── file_store.py        # 上传原始字节落盘（按需 OCR 取回；过期清理）
│   │   │   ├── page_image.py        # 扫描件某页渲染成 PNG（审核页对照原件，专用线程池）
│   │   │   ├── file_parser.py       # PDF/Word 文本提取调度
│   │   │   ├── ocr_engine.py        # Qwen-VL-OCR（DashScope）；RapidOCR 备用注释
│   │   │   ├── llm_client.py        # DeepSeek 封装（OpenAI SDK，结构化输出）
│   │   │   ├── prompt_loader.py     # 读取 prompts/，注入变量
│   │   │   ├── validators.py        # ② 确定性交叉校验
│   │   │   ├── derived_fields.py    # 律师确认的填写规则（原告/台数/利息/管辖/质保金等）
│   │   │   ├── branch_table.py      # 原告信息表：Excel 解析/校验/变更比对/存档回退
│   │   │   ├── branch_registry.py   # 按原告名称查信息表（信用代码/负责人/住址）
│   │   │   ├── equipment_list.py    # 合同设备清单中 VGE 家用电梯计数
│   │   │   ├── payment_nodes.py     # 付款条款拆节点、累计比例、匹配「已付 + 欠款」
│   │   │   ├── confidence.py        # ③ 可信度评分
│   │   │   ├── anchoring.py         # 值回原文命中/定位
│   │   │   ├── auth_store.py        # 账号与会话（SQLite，scrypt 哈希；令牌只存摘要）
│   │   │   ├── login_guard.py       # 登录输错锁定
│   │   │   ├── run_log.py           # 运行日志（JSONL：节点耗时/断点/LLM token/OCR 页数/律师改动/登录账号名；只记 key 不记取值，`tests/test_log_secrecy.py` 守着；按天切分、保留 `RUN_LOG_RETENTION_DAYS`；律师改动对比 checkpoint 里交给律师时的字段，重启不丢；`scripts/usage_report.py` 按账号统计。#75：节点内的 llm_call / ocr_batch 自动带 `node`，llm_call 带耗时与提示词版本（模板文件名@哈希），ocr_batch 带单页耗时分布与超时 / 限流页数，节点出错记固定 `error_category`（`services/error_category.py`），另有 `dashscope_key_switch`（OCR 与 LLM 备用路径改用付费 key，`path` 区分）、resume 的 `wait_s`、过期仍停在断点的 `abandoned`；`scripts/cost_report.py` 按 `.env` 的 `COST_PRICES` 汇总每案 / 每节点 / 每模型成本、错误类别、断点等待中位数与放弃率，计算在 `services/cost_summary.py`；`services/daily_summary.py` 每天 8 点（北京时间）后汇总前一天写 `data/daily_summary/YYYY-MM-DD.md`，含 Key 连通性检查，超过 `ALERT_*` 阈值的列入「需要关注」，不含案件内容，目前只写文件、以后加邮件）
│   │   │   └── company_lookup.py    # 联网企业信息查询
│   │   └── config.py
│   ├── scripts/                     # 运维脚本（账号管理、反馈查看）+ 案件 eval（eval_cases.py、case_eval/）
│   ├── data/                        # 原告信息表当前版 + 历史版（管理员页面上传，不进 git）
│   ├── requirements.txt
│   └── .env.example                 # DEEPSEEK_API_KEY, DASHSCOPE_API_KEY
│
├── deploy/                          # nginx 配置（http/https）、cert.sh（Let's Encrypt 证书）、deploy.sh（部署 + 失败回滚，GitHub Actions 审批后调用；先构建，再暂停接收新分析、等进行中的分析结束（最多 `DEPLOY_DRAIN_TIMEOUT` 秒）再重启）、backup.sh（原告信息表 + 账号库每日备份与恢复演练，不备份案件数据）、中文部署指南
└── docker-compose.yml               # 一键部署：backend（不对外，含登录校验）+ web（nginx 静态页 + 反代 + HTTPS）
```

---

## 核心类型定义

```typescript
// frontend/src/lib/types.ts

/** 后端返回的单个文件解析结果 */
export interface ParsedFile {
  filename: string;
  identified_type: DocType;    // '审批表' | '合同' | '补充协议' | '验收报告' | '战略合同' | '未知'，上传页律师可改
  detected_type?: DocType;     // 系统原先识别的类型（律师改过时与 identified_type 不同）
  text: string;
  is_scanned: boolean;
  page_count: number;
}

/** POST /api/upload 的响应 */
export interface UploadResponse {
  files: ParsedFile[];
  can_proceed: boolean;
  missing_materials: string[];
  warnings: string[];
}

/** 字段来源通道（用于 confidence 计算与展示） */
export type SourceChannel = 'text' | 'ocr' | 'multimodal';

/** 单个字段值（③：扩展了 confidence 与定位锚点） */
export interface FieldValue {
  value: string | number | null;
  src: string;                 // 《文件名》+ 位置，如《安装合同.pdf》第3条
  page?: number;               // 来源页码
  anchor?: string;             // 原文命中片段（用于跳转高亮/反幻觉比对）
  channel?: SourceChannel;     // 来源通道
  confidence?: number;         // 0–100，软估计，仅供 UI 排序
}

/** 抽取结果 JSON */
export interface ExtractedFields {
  plaintiff_branch_raw: FieldValue;
  plaintiff_name_final: FieldValue;
  plaintiff_credit_code: FieldValue;
  plaintiff_person_in_charge: FieldValue;
  plaintiff_address: FieldValue;
  plaintiff_phone: FieldValue;
  defendant_name: FieldValue;
  defendant_credit_code: FieldValue;
  defendant_legal_rep: FieldValue;
  defendant_address: FieldValue;
  contacts: { name: FieldValue; phone: FieldValue }[];
  contract_no: FieldValue;
  contract_title: FieldValue;
  contract_sign_date: FieldValue;
  elevator_qty: FieldValue;
  elevator_qty_by_approval: FieldValue;
  elevator_qty_by_contract: FieldValue;
  elevator_qty_by_acceptance: FieldValue;
  total_amount: FieldValue;
  paid_amount: FieldValue;
  unpaid_amount: FieldValue;
  amount_currency: FieldValue;
  acceptance_latest_date: FieldValue;
  payment_clause_location: FieldValue;
  payment_clause_text: FieldValue;
  breach_interest_clause_location: FieldValue;
  breach_interest_rate_text: FieldValue;
  dispute_clause_location: FieldValue;
  dispute_clause_text: FieldValue;
  project_site: FieldValue;
  internet_lookup_status: FieldValue;
  interest_rate_basis?: FieldValue;   // 派生：逾期利息计算标准
  court_district?: FieldValue;        // 派生：管辖法院辖区
  contract_type?: FieldValue;         // 审批表「合同类型」
  payment_clause_summary?: FieldValue; // AI 归纳的付款条款
  payment_cite_location?: FieldValue; // 派生：欠款对应的付款节点条款号
  payment_cite_text?: FieldValue;     // 派生：欠款对应的付款节点原文（写入起诉状）
  retention_ratio?: FieldValue;       // 质保金比例
  payable_ratio?: FieldValue;         // 派生：应付至合同款比例
  jurisdiction_text?: FieldValue;     // 派生：管辖段落
  contract_party_b?: FieldValue;      // 合同盖章页乙方
  elevator_qty_contract?: FieldValue; // 派生：约定台数（合同）
  elevator_qty_vge?: FieldValue;      // 合同中 VGE 家用电梯台数
  handover_text?: FieldValue;         // 派生：移交 / 结算表述
  retention_clause_text?: FieldValue; // 质保金条款原文
  retention_unpaid_amount?: FieldValue; // 审批表未付质保金
  document_kind?: FieldValue;         // 派生：民事起诉状 / 仲裁申请书
  arbitration_institution?: FieldValue; // 仲裁机构
  addressee?: FieldValue;             // 派生：致送法院 / 仲裁委
  breach_clause_text?: FieldValue;    // 甲方逾期付款违约条款原文
  interest_term?: FieldValue;         // 派生：违约金 / 逾期付款利息
  breach_clause_sentence?: FieldValue; // 派生：约定违约金时引用违约条款的一句
  paid_note?: FieldValue;             // 派生：已付款占合同款比例
  unpaid_note?: FieldValue;           // 派生：欠款比例，或（到期金额-已付）算式
  delivery_place?: FieldValue;        // 买卖合同交货地点
  install_address?: FieldValue;       // 验收报告安装地点（推断管辖用）
  contract_sign_place?: FieldValue;   // 合同盖章页「合同签订地」（推断管辖用）
  penalty_amount?: FieldValue;        // 派生：违约金金额（一次性；或按日计付的暂计金额）
  penalty_cap?: FieldValue;           // 派生：违约金 / 利息上限「，最高不超过……」
  defendant_address_ref?: FieldValue; // 审批表买方地址 / 合同甲方地址（仅推断管辖）
  penalty_daily_text?: FieldValue;    // 派生：按日计付标准「每日N元」
  penalty_start_date?: FieldValue;    // 派生：按日违约金起算日
  penalty_until_date?: FieldValue;    // 派生：暂计至（生成当日）
  warranty_period?: FieldValue;       // 合同质保期
  contract_party_a?: FieldValue;      // 合同盖章页甲方 / 买方（被告名称以此为准）
  contract_sign_place_cover?: FieldValue; // 合同封面「合同订立地点」
  handover_clause_text?: FieldValue;  // 合同约定移交对象的条款
  payable_amount?: FieldValue;        // 派生：「支付至X%合同款¥…元」的金额
  debt_term?: FieldValue;             // 派生：剩余合同款 / 到期合同款
  warranty_expiry_sentence?: FieldValue; // 派生：「，质保期已于…到期」
  supplement_agreement?: FieldValue;  // 上传材料中的补充协议文件名
  supplement_sentence?: FieldValue;   // 派生：补充协议变更合同总价的一句
  contract_total_in_contract?: FieldValue; // 合同正文约定的原合同总价
  contract_total_stated?: FieldValue; // 派生：起诉状「合同总价为¥…」（有补充协议时为原价）
}

/** 交叉校验单项结果（②） */
export interface ValidationCheck {
  key: string;                 // 'amount_reconcile' | 'qty_consistency' | ...
  passed: boolean;
  message: string;             // 中文说明
  related_fields: string[];
}

/** agent 命中的待决断点（interrupt） */
export interface PendingDecision {
  kind: 'missing' | 'conflict' | 'confirm';
  question: string;            // 中文，问律师
  options?: string[];          // 各字段共用的候选值（台数 / 质保金确认）
  field_keys: string[];
  field_options?: Record<string, { value: string; source: string }[]>; // 冲突断点：各字段的候选值及来源
}

/** agent 会话状态（贯穿 analyze/resume） */
export interface CaseState {
  run_id: string;
  extracted_fields: ExtractedFields;
  validations: ValidationCheck[];
  validation_report: string;   // 高亮说明文本（LLM 生成，仅展示）
  highlight_list: string;
  readiness: number;           // 起诉状就绪度 0–100
  pending: PendingDecision | null;
}

/** POST /api/analyze / /api/resume 的响应 */
export interface AnalyzeResponse {
  state: CaseState;
}

/** POST /api/generate 的响应 */
export interface GenerateResponse {
  complaint_text: string;
}

/** 字段在审核表格中的状态 */
export type FieldStatus = 'normal' | 'missing' | 'conflict' | 'ocr_uncertain';

/** 审核表格中的单行 */
export interface ReviewField {
  key: string;
  label: string;
  value: string | null;
  src: string;
  status: FieldStatus;
  confidence?: number;
  isEditing: boolean;
  editedValue?: string;
}

/** 流程步骤 */
export type FlowStep = 'upload' | 'processing' | 'review' | 'preview';

/** 处理子步骤 */
export type ProcessingSubStep = 'checklist' | 'extracting' | 'validating';
```

---

## 字段名中英映射

```typescript
// frontend/src/lib/field-map.ts

export const fieldNameMap: Record<string, string> = {
  plaintiff_branch_raw: '原告分公司（原文）',
  plaintiff_name_final: '原告名称',
  plaintiff_credit_code: '原告统一社会信用代码',
  plaintiff_person_in_charge: '原告负责人',
  plaintiff_address: '原告住址',
  plaintiff_phone: '原告电话',
  defendant_name: '被告名称',
  defendant_credit_code: '被告统一社会信用代码',
  defendant_legal_rep: '被告法定代表人',
  defendant_address: '被告住址',
  contacts: '联系人',
  contract_no: '合同编号',
  contract_title: '合同标题',
  contract_sign_date: '签约日期',
  elevator_qty: '台数（验收，写入起诉状）',
  elevator_qty_by_approval: '台数（审批表）',
  elevator_qty_by_contract: '台数（合同）',
  elevator_qty_by_acceptance: '台数（验收报告）',
  total_amount: '合同总价',
  paid_amount: '已付金额',
  unpaid_amount: '未付金额',
  amount_currency: '币种',
  acceptance_latest_date: '最晚验收日期',
  payment_clause_location: '付款条款位置',
  payment_clause_text: '付款条款原文',
  breach_interest_clause_location: '违约利率条款位置',
  breach_interest_rate_text: '违约利率',
  dispute_clause_location: '争议解决条款位置',
  dispute_clause_text: '争议解决条款内容',
  project_site: '工程地点',
  internet_lookup_status: '联网查询状态',
  interest_rate_basis: '逾期利息计算标准',
  court_district: '管辖法院（辖区）',
  contract_type: '合同类型',
  payment_clause_summary: '付款条款（AI 归纳）',
  payment_cite_location: '引用的付款节点（写入起诉状）',
  payment_cite_text: '付款节点原文（写入起诉状）',
  retention_ratio: '质保金比例',
  payable_ratio: '应付至合同款比例',
  jurisdiction_text: '管辖段落',
  contract_party_b: '合同乙方（盖章页）',
  elevator_qty_contract: '台数（约定，写入起诉状）',
  elevator_qty_vge: 'VGE 家用电梯台数',
  handover_text: '移交 / 结算表述',
  retention_clause_text: '质保金条款',
  retention_unpaid_amount: '审批表未付质保金',
  document_kind: '文书类型',
  arbitration_institution: '仲裁机构',
  addressee: '致送（法院 / 仲裁委）',
  breach_clause_text: '违约条款原文',
  interest_term: '违约金 / 逾期付款利息',
  breach_clause_sentence: '违约条款引用（写入起诉状）',
  paid_note: '已付款比例',
  unpaid_note: '欠款比例 / 算式',
  delivery_place: '交货地点',
  install_address: '安装地点（验收报告）',
  contract_sign_place: '合同签订地',
  penalty_amount: '违约金金额（写入诉请）',
  penalty_cap: '违约金 / 利息上限（写入诉请）',
  defendant_address_ref: '被告地址（审批表 / 合同，仅推断管辖）',
  penalty_daily_text: '按日违约金标准',
  penalty_start_date: '违约金起算日',
  penalty_until_date: '违约金暂计至',
  warranty_period: '质保期',
  contract_party_a: '合同甲方（盖章页）',
  contract_sign_place_cover: '合同订立地点（封面）',
  handover_clause_text: '移交条款（合同）',
  payable_amount: '应付金额（写入「支付至…合同款」）',
  debt_term: '剩余 / 到期合同款',
  warranty_expiry_sentence: '质保期到期句（写入起诉状）',
  supplement_agreement: '补充协议（上传材料）',
  supplement_sentence: '补充协议表述（写入起诉状）',
  contract_total_in_contract: '合同正文约定总价',
  contract_total_stated: '合同总价（写入起诉状）',
};

/** 审核表格中展示的字段 */
export const reviewFieldKeys: string[] = [
  'plaintiff_name_final',
  'plaintiff_credit_code',
  'plaintiff_person_in_charge',
  'plaintiff_address',
  'plaintiff_phone',
  'defendant_name',
  'defendant_credit_code',
  'defendant_legal_rep',
  'defendant_address',
  'defendant_address_ref',
  'contacts',
  'contract_no',
  'contract_title',
  'document_kind',
  'contract_type',
  'contract_party_b',
  'contract_sign_date',
  'elevator_qty_contract',
  'elevator_qty',
  'elevator_qty_vge',
  'total_amount',
  'paid_amount',
  'unpaid_amount',
  'paid_note',
  'unpaid_note',
  'acceptance_latest_date',
  'retention_ratio',
  'retention_clause_text',
  'retention_unpaid_amount',
  'payable_ratio',
  'handover_text',
  'payment_cite_location',
  'payment_cite_text',
  'payment_clause_location',
  'payment_clause_summary',
  'payment_clause_text',
  'breach_interest_clause_location',
  'breach_clause_text',
  'interest_term',
  'breach_interest_rate_text',
  'interest_rate_basis',
  'penalty_cap',
  'penalty_amount',
  'penalty_daily_text',
  'penalty_start_date',
  'penalty_until_date',
  'warranty_period',
  'dispute_clause_location',
  'dispute_clause_text',
  'project_site',
  'delivery_place',
  'contract_sign_place',
  'install_address',
  'court_district',
  'jurisdiction_text',
  'arbitration_institution',
  'addressee',
];
```

---

## 后端配置

```python
# backend/app/config.py

import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")  # 旧名 deepseek-v4-flash 为临时别名

LLM_TEMPERATURE = 0.1
# DeepSeek Flash 是推理模型：思维链（reasoning_content）也计入 max_tokens。
# 8192 时曾出现思维链耗尽全部配额、正文为空（finish_reason=length），故上调到 16384。
LLM_MAX_TOKENS = 16384
LLM_TIMEOUT = 120  # 秒（读取超时）
LLM_TOTAL_TIMEOUT = 180  # 秒（单次调用总时限，超过按不可用处理）

# DeepSeek 不可用时的备用模型（DashScope OpenAI 兼容接口，key 与 OCR 共用）；设为空关闭
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "qwen3.8-flash")
LLM_FALLBACK_COOLDOWN_S = 600  # DeepSeek 出错后 10 分钟内直接用备用模型

# Qwen-VL-OCR（阿里云 DashScope）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_API_KEY_BACKUP = os.getenv("DASHSCOPE_API_KEY_BACKUP")  # 付费备用：主 key 被拒（401/403）时改用，429 不切换
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
QWEN_OCR_MODEL = os.getenv("QWEN_OCR_MODEL", "qwen3.8-flash")  # 关闭思考模式调用；可改 qwen3.8-max / qwen-vl-ocr

OCR_TIMEOUT = 120  # 秒
OCR_GLOBAL_CONCURRENCY = 8  # 全服务同时识别的页数上限（所有律师共用，超出排队；OCR 用专用线程池，不挤占登录校验等请求的默认线程池）

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50
SCANNED_PAGE_TEXT_THRESHOLD = 20  # 单页少于此字数算扫描页；半数以上页是扫描页的 PDF 按扫描件处理

# Agent 护栏
AGENT_MAX_OCR_PAGES = 50      # 扫描合同按需 OCR 的页数上限（条款 / 签章页 / 质保期找到即停；contract_scan 补读设备清单时已读页也计入，合计不超过 50）
AGENT_MAX_REPORT_OCR_PAGES = 30  # 扫描验收报告按需 OCR 的页数上限（与合同分开计）
AGENT_MAX_APPROVAL_OCR_PAGES = 8  # 扫描审批表在清点前整份 OCR 的页数上限（`approval_ocr` 节点，全案共用）
AGENT_MAX_STRATEGIC_OCR_PAGES = 10  # 扫描战略 / 框架合同另给的页数上限（不被主合同的 50 页用完）
AGENT_MAX_TOTAL_OCR_PAGES = 80  # 单次分析所有按需 OCR 合计上限（上面各项是子上限，共用这 80 页；state.ocr_pages_used 计数）
# 依据：11 个真实案卷每案 5–56 页。合同页数用完仍缺条款时，出处与起诉状【待补充】后注明「扫描识别页数已达上限，合同可能未读完」，
# 不当作合同没约定（不加断点，律师看提示自行查原件）。
# 另：上传时文件名认不出类型的扫描件各 OCR 第 1 页认标题（不计入上面的上限）；部分扫描 PDF 有文字层的页不 OCR、不计入

# 可信度阈值
CONFIDENCE_UNCERTAIN_BELOW = 60   # 低于此值标记为"待核实"

# 原告信息表（管理员上传维护，不进 git）
BRANCH_INFO_PATH = os.getenv("BRANCH_INFO_PATH", "backend/data/branch_info.json")
BRANCH_HISTORY_DIR = os.getenv("BRANCH_HISTORY_DIR", "backend/data/branch_history")
BRANCH_TABLE_STALE_DAYS = 90
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")   # 管理员口令；未设置则不能更新原告信息表
PLAINTIFF_HQ_REP_TITLE = "董事长"         # 只有总公司在法定代表人姓名后写职务

# CORS 允许的前端地址
CORS_ORIGINS = ["http://localhost:5173"]
```

```
# backend/.env.example
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
# 阿里云 DashScope，用于 Qwen 多模态模型识别扫描件 PDF
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
# 可选：付费备用 key（主 key 免费额度被拒时自动改用）
# DASHSCOPE_API_KEY_BACKUP=sk-xxxxxxxxxxxxxxxx
# 管理员口令：更新原告信息表时输入
ADMIN_TOKEN=
# 运行日志（backend/data/run_log.jsonl，不进 git）；设为 0 关闭
RUN_LOG_ENABLED=1
# 运行日志按天切分，旧日志保留天数（不含案件内容，故比案件数据保留得久）
RUN_LOG_RETENTION_DAYS=90
# 案件数据保留天数（checkpoint + 上传文件），超期自动清除
CASE_RETENTION_DAYS=7
```

---

## Prompt 文件对照表

| Prompt 文件（`/prompts/`） | 后端加载位置 | 步骤 | LLM 输出格式 |
|---|---|---|---|
| `prompt-a-extract.md` | `agent/nodes.py` via `prompt_loader.py` | 字段抽取 | JSON（结构化输出） |
| `prompt-a-validate.md` | `agent/nodes.py` via `prompt_loader.py` | 仅生成高亮说明文本 | 文本 |
| `prompt-a-clauses.md` | `agent/nodes.py` via `prompt_loader.py` | 按需 OCR 后补抽条款 | JSON |
| `prompt-b-generate.md` | v1 遗留，代码不再加载 | — | — |
| `complaint-template.md` | `services/`（模板常量） | 模板 | — |

> 材料清点不调 LLM（`services/materials.py`）。
