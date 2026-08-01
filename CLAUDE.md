# CLAUDE.md — 安装合同纠纷起诉状自动化系统（v1）

本文件是 AI 编码助手（Claude / Copilot / Cursor）的项目指令文件，包含项目上下文、代码规范和开发约束。

---

## 项目概述

面向律师团队的 Web 应用，用于自动化处理安装合同纠纷案件的民事起诉状准备工作。律师上传案件文件，系统自动完成信息抽取、交叉校验，律师确认后生成起诉状 Word 文档。

**核心用户**：中国大陆律师团队，不懂技术，不看 JSON，不看英文。所有面向用户的内容必须是中文。

**v1 目标**：验证 AI 抽取准确率和律师使用体验。支持可解析 PDF、扫描件 PDF（OCR）和 Word 文件。无数据库，无用户认证，案件不持久化。

---

## 核心原则

- **Reusability**：优先复用已有组件和工具函数，不重复造轮子
- **Stability**：任何改动不得破坏现有功能，改动前先理解上下文
- **Quality Assurance**：所有新功能必须有完整的类型定义，关键逻辑需有注释说明意图
- **AI 不做最终判断**：AI 只负责抽取和校验，律师负责确认和修改
- **出处可追溯**：每个字段值必须标注来源文件和位置
- **宁缺勿造**：无法确定的字段标为缺失，绝不编造

---

## 技术栈

### 前端

```
框架：       React 19 + Vite + TypeScript（strict mode）
样式：       Tailwind CSS
UI 组件：    shadcn/ui
表单：       react-hook-form + zod
文档生成：   docx（docx-js，浏览器端生成 Word）
路由：       无（单页面 Stepper 组件）
```

### 后端（Python，轻量）

```
框架：       FastAPI
文件解析：   PyMuPDF（fitz）提取 PDF 文本
             python-docx 提取 Word 文本
OCR：        Qwen-VL-OCR 云端 API（阿里云 DashScope）；备用 RapidOCR（本地 ONNX，代码已注释保留）
LLM 调用：   OpenAI SDK（兼容 DeepSeek API）
运行方式：   uvicorn，单进程，本地运行
```

### 为什么 v1 用 Python 后端而不是纯前端

- OCR（Qwen-VL-OCR 云端 API / RapidOCR 本地）需要在 Python 端调度，浏览器无法跑
- DeepSeek、DashScope 等 API Key 放后端，不暴露给前端
- 文件解析（PDF/Word）在后端更稳定，库更成熟
- 前端只负责 UI，后端负责所有"重活"

---

## 项目结构

```
legal-doc-app/
├── CLAUDE.md
├── prompts/                         # LLM Prompt 模板（纯文本，供参考和调试）
│   ├── prompt-a-checklist.md
│   ├── prompt-a-extract.md
│   ├── prompt-a-validate.md
│   ├── prompt-b-generate.md
│   └── complaint-template.md
│
├── frontend/                        # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/                  # shadcn/ui（自动生成，不手动修改）
│   │   │   ├── layout/              # Header, Container
│   │   │   ├── upload/              # FileDropzone, FileList
│   │   │   ├── processing/          # StepProgress
│   │   │   ├── review/              # FieldTable, FieldRow, ValidationReport, HighlightList
│   │   │   └── preview/             # ComplaintPreview
│   │   ├── hooks/
│   │   │   ├── useFileUpload.ts     # 文件上传到后端
│   │   │   └── useComplaintFlow.ts  # 整体流程状态管理
│   │   ├── lib/
│   │   │   ├── api.ts               # 后端 API 客户端
│   │   │   ├── doc-generator.ts     # Word 文档生成（docx-js）
│   │   │   ├── field-map.ts         # 字段名英中映射
│   │   │   ├── types.ts             # 共享类型定义
│   │   │   └── utils.ts             # 工具函数
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.css
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── tailwind.config.ts
│
├── backend/                         # Python FastAPI 后端
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口 + CORS
│   │   ├── routers/
│   │   │   ├── files.py             # POST /api/upload — 接收文件、解析、OCR
│   │   │   ├── extract.py           # POST /api/extract — 调 LLM 做抽取+校验
│   │   │   └── generate.py          # POST /api/generate — 调 LLM 生成起诉状
│   │   ├── services/
│   │   │   ├── file_parser.py       # PDF/Word 文本提取调度
│   │   │   ├── ocr_engine.py        # Qwen-VL-OCR（DashScope）封装；RapidOCR 备用代码注释保留
│   │   │   ├── llm_client.py        # DeepSeek API 封装（OpenAI SDK）
│   │   │   └── prompt_loader.py     # 读取 prompts/ 目录，注入变量
│   │   └── config.py                # 配置（API Key、端口等）
│   ├── requirements.txt
│   └── .env.example                 # DEEPSEEK_API_KEY=sk-xxx, DASHSCOPE_API_KEY=sk-xxx
│
└── docker-compose.yml               # （可选）一键启动前端+后端
```

---

## API 路由设计（后端）

v1 只有 3 个接口，极简：

```
POST /api/upload
  输入：multipart/form-data（多个文件）
  处理：解析每个文件 → 可解析PDF用PyMuPDF → 扫描件用Qwen-VL-OCR（DashScope）→ Word用python-docx
  输出：{
    files: [{ filename, identified_type, text, is_scanned, page_count }],
    can_proceed: boolean,
    missing_materials: []
  }

POST /api/extract
  输入：{ files_text: string[], internet_allowed: boolean }
  处理：拼装 Prompt A（清点+抽取+校验）→ 调 DeepSeek API（可拆为多次调用）
  输出：{
    extracted_fields: {...},      # JSON
    validation_report: string,    # 校验报告文本
    highlight_list: string        # 高亮列表文本
  }

POST /api/generate
  输入：{ validated_json: {...} }
  处理：拼装 Prompt B + 模板 → 调 DeepSeek API
  输出：{
    complaint_text: string        # 起诉状全文
  }
```

Word 文件生成在前端用 docx-js 完成（避免后端装 Node 依赖）。

---

## 代码规范

### Codebase Management

- **Code Standards**：
  - 前端：TypeScript strict mode，不允许 `any`
  - 后端：Python 3.10+，使用 type hints，Pydantic 做请求/响应模型
- **Documentation**：每个文件顶部写一行注释说明职责；复杂业务逻辑必须有注释说明意图；代码变更时同步更新相关注释
- **Formatting**：
  - 前端：Prettier（`printWidth: 100`, `singleQuote: true`, `semi: true`）+ ESLint
  - 后端：Ruff（lint + format）
  - 导入顺序：标准库 → 第三方 → 项目内
- **Version Control**：提交粒度以功能点为单位，提交信息说明改动内容

### Feature Implementation

- **Leverage Existing Assets**：新功能优先检查 shadcn/ui 是否有现成组件；业务逻辑放 `lib/`（前端）或 `services/`（后端），不写在组件/路由里
- **Component Patterns**：函数组件 + hooks；状态提升到最近共同父组件；shadcn/ui 组件不手动修改源码
- **Naming**：
  - 前端：组件 PascalCase（`FieldTable.tsx`），工具 kebab-case（`field-map.ts`）
  - 后端：模块 snake_case（`ocr_engine.py`），类 PascalCase，函数 snake_case
  - 每个文件单一职责，组件不超过 200 行

### Error Handling

- LLM API 调用：timeout 120s，失败重试 1 次，返回中文错误信息
- OCR：timeout 60s，失败返回 `{ error: "OCR 处理失败：具体原因" }`
- 文件解析：不支持的格式返回明确错误，不静默失败
- JSON 解析：LLM 返回可能有 markdown 代码块标记，需去除后重试
- 前端所有用户提示一律中文

### Documentation Best Practices

- **Location**：CLAUDE.md 在项目根目录；Prompt 在 `prompts/`；前端文档在 `frontend/` 内；后端文档在 `backend/` 内
- **Change Logs**：优先更新已有文件，不另建新文件

---

## 应用流程

### 用户视角（单页面 Stepper，共 4 步）

```
Step 1: 上传文件
  - 拖拽/点击上传，支持 .pdf / .docx
  - 联网查询开关（默认开启）
  - "开始分析"按钮
  → 前端调 POST /api/upload

Step 2: AI 处理中
  - 步骤条：解析文件 → 抽取字段 → 交叉校验
  - 每步完成显示摘要（"识别到3份文件""抽取到23个字段""发现2个冲突"）
  - 出错 → 中文错误信息 + 重试按钮
  → 前端调 POST /api/extract

Step 3: 审核确认 ⭐ 核心页面
  - 左侧：字段表格
    - 列：字段名（中文）| 值 | 来源出处 | 状态
    - 状态标签：✅ 正常 / ❌ 缺失 / ⚠️ 冲突 / 🔍 待核实（OCR）
    - 所有字段可点击编辑
    - 冲突字段展开各来源值，律师选择
  - 右侧：校验报告 + 高亮列表
  - 底部："确认并生成起诉状"

Step 4: 预览下载
  - 起诉状全文预览（高亮标注保留）
  - "下载 Word"（前端 docx-js 生成）
  - "返回修改"按钮
  → 前端调 POST /api/generate → 拿到文本 → docx-js 生成 Word
```

### 技术视角（数据流）

```
用户上传文件
    │
    ▼
POST /api/upload → 后端接收文件
    ├── .docx → python-docx → text
    ├── .pdf → PyMuPDF → 判断是否扫描件
    │     ├── 有文字 → text
    │     └── 无文字（扫描件）→ Qwen-VL-OCR（DashScope）→ text
    └── 返回：每个文件的 text + 类型 + 是否扫描件
    │
    ▼
POST /api/extract → 后端拼装 Prompt
    │
    ├── Prompt A-1（材料清点）→ DeepSeek API → MaterialChecklist
    │     └── can_proceed = false → 返回缺失提示
    │
    ├── Prompt A-2（字段抽取）→ DeepSeek API → ExtractedFields JSON
    │
    └── Prompt A-3（校验高亮）→ DeepSeek API → 校验报告 + 高亮列表
    │
    ▼
前端渲染：JSON → 中文字段表格（代码转换）
律师编辑/确认字段
    │
    ▼
POST /api/generate → 后端拼装 Prompt B + 确认后 JSON
    → DeepSeek API → 起诉状全文
    │
    ▼
前端：docx-js 生成 Word → 下载
```

---

## 核心类型定义

```typescript
// frontend/src/lib/types.ts

/** 后端返回的单个文件解析结果 */
export interface ParsedFile {
  filename: string;
  identified_type: '审批表' | '合同' | '验收报告' | '未知';
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

/** 单个字段值（AI 抽取格式） */
export interface FieldValue {
  value: string | number | null;
  src: string;
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

/** POST /api/extract 的响应 */
export interface ExtractResponse {
  extracted_fields: ExtractedFields;
  validation_report: string;
  highlight_list: string;
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
  isEditing: boolean;
  editedValue?: string;
}

/** 流程步骤 */
export type FlowStep = 'upload' | 'processing' | 'review' | 'preview';

/** 处理子步骤 */
export type ProcessingSubStep = 'parsing' | 'extracting' | 'validating';
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
  elevator_qty: '电梯台数（最终）',
  elevator_qty_by_approval: '台数（审批表）',
  elevator_qty_by_contract: '台数（合同）',
  elevator_qty_by_acceptance: '台数（验收报告）',
  total_amount: '合同总价',
  paid_amount: '已付金额',
  unpaid_amount: '未付金额',
  amount_currency: '币种',
  acceptance_latest_date: '最晚验收日期',
  payment_clause_location: '付款条款位置',
  payment_clause_text: '付款条款内容',
  breach_interest_clause_location: '违约利率条款位置',
  breach_interest_rate_text: '违约利率',
  dispute_clause_location: '争议解决条款位置',
  dispute_clause_text: '争议解决条款内容',
  project_site: '工程地点',
  internet_lookup_status: '联网查询状态',
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
  'contacts',
  'contract_no',
  'contract_title',
  'contract_sign_date',
  'elevator_qty',
  'total_amount',
  'paid_amount',
  'unpaid_amount',
  'acceptance_latest_date',
  'payment_clause_text',
  'breach_interest_rate_text',
  'dispute_clause_text',
  'project_site',
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
DEEPSEEK_MODEL = "deepseek-chat"

LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 8192
LLM_TIMEOUT = 120  # 秒

# Qwen-VL-OCR（阿里云 DashScope）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
QWEN_OCR_MODEL = "qwen-vl-ocr-latest"

OCR_TIMEOUT = 120  # 秒（云端 API，单页通常 < 60s）

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50

# CORS 允许的前端地址
CORS_ORIGINS = ["http://localhost:5173"]
```

```
# backend/.env.example
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
# 阿里云 DashScope，用于 Qwen-VL-OCR 识别扫描件 PDF
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

---

## Prompt 文件对照表

| Prompt 文件（`/prompts/`） | 后端加载位置 | 步骤 | LLM 输出格式 |
|---|---|---|---|
| `prompt-a-checklist.md` | `prompt_loader.py` | 材料清点 | JSON |
| `prompt-a-extract.md` | `prompt_loader.py` | 字段抽取 | JSON |
| `prompt-a-validate.md` | `prompt_loader.py` | 校验高亮 | 文本 |
| `prompt-b-generate.md` | `prompt_loader.py` | 起诉状生成 | 文本 |
| `complaint-template.md` | `prompt_loader.py` | 模板常量 | — |

后端 `prompt_loader.py` 负责读取 markdown 文件、注入 `{{变量}}`、返回完整 Prompt 字符串。

---

## 本地开发启动

```bash
# 1. 启动后端
cd backend
pip install -r requirements.txt
cp .env.example .env       # 填入 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 2. 启动前端
cd frontend
pnpm install
pnpm dev                   # 默认 http://localhost:5173

# 前端通过 vite.config.ts 的 proxy 将 /api/* 转发到 localhost:8000
```

---

## v1 不包含（后续迭代预留）

- 数据库（PostgreSQL）— 案件持久化
- 用户认证 / 权限管理
- 多模态 LLM 直传扫描件（替代 OCR）
- 批量案件处理
- Node.js 后端（Fastify）— 若需要替换 Python
- BullMQ 异步任务队列
- S3 / 云存储
- 起诉状版本对比
- 操作审计日志
- Docker 化部署
