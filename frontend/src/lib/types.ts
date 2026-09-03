// 共享类型定义：所有接口、枚举、联合类型均在此声明，前后端数据契约的前端侧

/** 单页文本（供抽取值回原文定位到具体页码） */
export interface PageText {
  page: number;
  text: string;
}

/** 后端返回的单个文件解析结果 */
export interface ParsedFile {
  filename: string;
  identified_type: '审批表' | '合同' | '验收报告' | '未知';
  text: string;
  is_scanned: boolean;
  page_count: number;
  pages: PageText[];
}

/** POST /api/upload 的响应 */
export interface UploadResponse {
  files: ParsedFile[];
  can_proceed: boolean;
  missing_materials: string[];
  warnings: string[];
}

/** GET /api/extract/progress 的响应：三步 LLM 处理的实时阶段（前端轮询用） */
export interface LlmProgress {
  active: boolean;
  stage: '材料清点' | '字段抽取' | '校验高亮' | '';
  stage_index: number;
  total_stages: number;
  stage_elapsed_s: number;
}

/** GET /api/upload/progress 的响应：上传处理期间的实时进度（前端轮询用） */
export interface UploadProgress {
  active: boolean;
  filename: string | null;
  file_index: number;
  total_files: number;
  stage: '解析中' | 'OCR识别中' | '';
  done_pages: number;
  total_pages: number;
}

/** 字段来源通道（用于 confidence 计算与展示） */
export type SourceChannel = 'text' | 'ocr' | 'multimodal';

/** 单个字段值（AI 抽取格式；出处字段由后端逐页锚定补充，供回溯） */
export interface FieldValue {
  value: string | number | null;
  src: string;
  page?: number | null; // 值回原文命中的真实页码（未命中为 null）
  anchor?: string | null; // 原文命中片段（反幻觉比对 / 高亮）
  channel?: SourceChannel; // 命中所在文件的来源通道
  confidence?: number; // 0–100 软置信度，仅供 UI 排序/默认展开
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
}

/** 交叉校验单项结果（②：由后端确定性代码判定，非 LLM） */
export interface ValidationCheck {
  key: string; // 'amount_reconcile' | 'qty_consistency' | 'credit_code' | 'date_valid'
  passed: boolean;
  applicable: boolean; // false 表示输入不足、本次跳过（既非通过也非冲突）
  is_conflict: boolean; // applicable 且未通过 = 真冲突
  message: string; // 中文说明
  related_fields: string[];
}

/** POST /api/extract 的响应 */
export interface ExtractResponse {
  extracted_fields: ExtractedFields;
  validations: ValidationCheck[]; // 确定性校验结论，冲突态的权威来源
  validation_report: string;
  highlight_list: string;
}

/** agent 命中的待决断点（interrupt）——审核页据此渲染让律师决定 */
export interface PendingDecision {
  kind: 'missing' | 'conflict' | 'confirm';
  question: string; // 中文，问律师
  options: string[]; // 冲突时的候选值（可空，律师可自由编辑）
  field_keys: string[]; // 涉及的字段 key
}

/**
 * agent 会话状态：POST /api/analyze、/api/resume 的响应体。
 * 是 ExtractResponse 的超集，额外携带 run_id（供 resume 定位会话）、
 * readiness（起诉状就绪度 0–100）、pending（断点，null 表示无待决）。
 */
export interface CaseState {
  run_id: string;
  extracted_fields: ExtractedFields;
  validations: ValidationCheck[];
  validation_report: string;
  highlight_list: string;
  readiness: number;
  pending: PendingDecision | null;
}

/** POST /api/generate 的响应 */
export interface GenerateResponse {
  complaint_text: string;
}

/**
 * 起诉状文档：LLM 生成的起诉状全文（含【高亮缺失】/【高亮冲突】/⚠️ 待核实 等审核期标记）。
 * 这些标记仅用于屏幕预览着色（见 ComplaintPreview.tsx），Word 导出时会被清理为纯文本。
 */
export type ComplaintDocument = string;

/** Word 导出的页眉/页脚元信息 */
export interface DocMeta {
  /** Text shown in the running header, e.g. the case name */
  headerText: string;
  /** Optional right-aligned header text, e.g. firm name */
  headerRight?: string;
}

/** 字段在审核表格中的状态 */
export type FieldStatus = 'normal' | 'missing' | 'conflict' | 'ocr_uncertain';

/** 审核表格中的单行 */
export interface ReviewField {
  key: string;
  label: string;
  value: string | null;
  src: string;
  page?: number | null; // 值回原文命中的真实页码（供来源列展示，未命中为 null）
  status: FieldStatus;
  isEditing: boolean;
  editedValue?: string;
}

/** 流程步骤 */
export type FlowStep = 'upload' | 'processing' | 'review' | 'preview';

/** 处理子步骤 */
export type ProcessingSubStep = 'parsing' | 'extracting' | 'validating';
