# CLAUDE.md — 安装合同纠纷起诉状自动化系统（v2 · Agentic）

本文件是 AI 编码助手（Claude / Copilot / Cursor）的项目指令文件，包含项目上下文、代码规范和开发约束。

> **v1 → v2 变更提示**：v1 是"固定流水线"（`upload → 清点 → 抽取 → 校验 → 生成`，控制流写死）。v2 将核心处理改为 **agentic**：由一个带工具的 agent 驱动控制流，按需读取材料、自校验、在关键节点暂停问律师。本文件描述的是 v2 目标架构；标注「v1 遗留」的段落为迁移前的旧实现，逐步替换。

---

## 项目概述

面向律师团队的 Web 应用，用于自动化处理安装合同纠纷案件的民事起诉状准备工作。律师上传案件文件，agent 自动完成材料清点、信息抽取、交叉校验，律师确认后生成起诉状 Word 文档。

**核心用户**：中国大陆律师团队，不懂技术，不看 JSON，不看英文。所有面向用户的内容必须是中文。

**目标**：验证 AI 抽取准确率和律师使用体验。支持可解析 PDF、扫描件 PDF（OCR）和 Word 文件。无数据库，无用户认证，案件不持久化（agent 状态仅在单次会话内存活）。

---

## 核心原则

- **Reusability**：优先复用已有组件和工具函数，不重复造轮子
- **Stability**：任何改动不得破坏现有功能，改动前先理解上下文
- **Quality Assurance**：所有新功能必须有完整的类型定义，关键逻辑需有注释说明意图
- **AI 不做最终判断**：agent 只负责抽取、校验、提议，律师负责确认和修改；关键分歧点 agent 必须暂停问律师（human-in-the-loop），不得自行拍板
- **出处可追溯**：每个字段值必须标注来源文件 + 位置（页码/锚点），且值必须能在原文精确命中
- **宁缺勿造**：无法确定的字段标为缺失（value=null），绝不编造；抽取值回原文比对失败一律置空
- **校验用代码，不用 LLM**（①②）：可量化的一致性检查——金额勾稽、台数三源比对、格式校验——一律由确定性 Python 代码完成，LLM 不做算术判断。LLM 只负责"抽出数字"，"比对"由代码算
- **可信度分层**（③）：硬校验（是/否结论）与软估计（0–100 confidence）分开；confidence 只用于 UI 排序和默认展开，不替代律师判断
- **模板渲染，不自由生成**（④）：起诉状是固定法律格式，由结构化字段 + docx 模板确定性渲染，LLM 不写全文

---

## 技术栈

### 前端

```
框架：       React 19 + Vite + TypeScript（strict mode）
样式：       Tailwind CSS
UI 组件：    shadcn/ui
表单：       react-hook-form + zod
文档生成：   docx（docx-js，浏览器端按模板填槽生成 Word）
路由：       无（单页面 Stepper 组件）
实时进度：   SSE / fetch stream（接收 agent 的分步进展与暂停点）
```

### 后端（Python）

```
框架：         FastAPI
Agent 编排：   LangGraph（状态图 + interrupt 断点 + checkpoint；仅用图与检查点，不引入整个 LangChain 生态）
文件解析：     PyMuPDF（fitz）提取 PDF 文本；python-docx 提取 Word 文本
OCR：          Qwen-VL-OCR 云端 API（阿里云 DashScope）；备用 RapidOCR（本地 ONNX，代码注释保留）
LLM 调用：     OpenAI SDK（兼容 DeepSeek API），启用 function calling / 结构化输出
模板渲染：     docxtpl（可选，后端渲染）或前端 docx-js（默认，见 API 设计）
企业信息查询： 联网工具（企查查/天眼查/公示系统 API，按信用代码补全法人全称）
运行方式：     uvicorn，单进程，本地运行
```

### 关于 agent 框架的取舍（①）

- **不引入 LangChain 本体**：抽象层深、prompt 不透明、版本易 breaking，对"可审计"要求高的法律场景是负担。结构化输出、重试等零件自行实现或用轻量库。
- **采用 LangGraph 的图 + checkpoint + interrupt**：正好承载"抽取→发现冲突→定位重读→暂停问律师→恢复"的控制流。v1 无持久化，用 in-memory checkpointer 即可；未来接 DB 时零重构。
- **工具层与校验层保持框架无关**：所有工具（解析、OCR、校验、查询、渲染）是纯 Python 函数/service，不依赖 LangGraph，便于单测与替换编排器。

### 为什么用 Python 后端

- OCR（Qwen-VL-OCR / RapidOCR）需在 Python 端调度，浏览器无法跑
- DeepSeek、DashScope、企业查询等 API Key 放后端，不暴露前端
- 文件解析（PDF/Word）在后端更稳定，库更成熟
- agent 循环、确定性校验、工具编排都在后端；前端只负责 UI 与律师交互

---

## 真实材料形态与来源可靠性分级 ⭐

本系统处理的一个典型案件通常含三类材料，**形态与可靠性差异很大，agent 必须区别对待**：

| 材料类别 | 典型文档 | 形态 | 可靠性 | agent 处理策略 |
|---|---|---|---|---|
| **审批表** | KA 大客户收款不良合同诉讼审批表 | 可解析文本、**键值表单** | ⭐⭐⭐ 最高（结构化，逐项直取） | **主数据源**：被告名称/地址/联系人、合同号、签约日期、台数（签约/实际）、合同总额、已付/未付、最后付款日等绝大多数字段直接取此 |
| **验收/检验报告** | 电梯监督检验报告（监检报告） | 可解析文本 + 盖章图；每台电梯一份 | ⭐⭐ 中高 | **交叉校验源**：被告统一社会信用代码、安装地点、检验合格日期（→最晚验收日）、台数（验收口径 = 报告份数/设备代码数） |
| **安装合同** | 电梯安装合同 | 常为**纯扫描件**（需 OCR，页数多） | ⭐ OCR 有噪声，需降权+待核实 | **条款文本源**：付款条款、违约利率条款、争议解决条款；台数（合同口径）。OCR 成本高，**按需只读相关页，不整份全抽** |

**由此确立的抽取策略（agentic 的核心价值）**：

1. 优先从**审批表**批量拿结构字段（便宜、可靠）；
2. 用**验收报告**补信用代码/安装地点/验收日期，并做台数、名称交叉校验；
3. 仅当仍缺条款类字段时，才对**扫描合同**做 OCR + 条款定位检索（关键词/向量），**只 OCR 需要的页**，避免整份扫描件全量 OCR 的成本与噪声；
4. 三源台数（签约/合同/验收）、金额勾稽（总额 = 已付 + 未付）由代码比对——真实样例 `638667.2 + 256266.8 = 894934` 即典型勾稽。

> 注：字段 `identified_type` 的"验收报告"在实务中常表现为"电梯监督检验报告"，识别时二者归为同一类。

---

## Agentic 架构

### 组成

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator（LangGraph 状态图）                          │
│  节点：清点 → 抽取(带工具) → 校验(代码) → [interrupt:律师] → 生成 │
│  边：条件跳转（缺材料/有冲突 → 回到抽取或暂停）                  │
└───────────────┬─────────────────────────────────────────┘
                │ 读/写
        ┌───────▼────────┐         ┌──────────────────────────┐
        │  CaseState      │◄───────►│  Tools（纯 Python service）│
        │ 字段/来源/       │         │  parse_file / ocr_page     │
        │ 置信度/冲突/     │         │  search_in_docs            │
        │ 未决问题        │         │  check_amounts / check_qty │
        └────────────────┘         │  validate_credit_code      │
                                    │  lookup_company（联网）      │
                                    │  render_complaint（模板）    │
                                    └──────────────────────────┘
```

### 工具清单（框架无关，`backend/app/services/` 下的纯函数）

| 工具 | 职责 | 备注 |
|---|---|---|
| `parse_file(name)` | PDF/Word → 文本 + 是否扫描件 + 页数 | PyMuPDF / python-docx |
| `ocr_page(file, page)` | 对**指定页**做 OCR | 按需调用，非整份；Qwen-VL-OCR |
| `search_in_docs(keyword)` | 回原文定位条款/字段所在页与片段 | 长合同用，省 token |
| `check_amounts(state)` | 总额 == 已付 + 已付；返回是否勾稽 | **确定性代码**，非 LLM |
| `check_elevator_qty(state)` | 签约/合同/验收三源台数比对 | **确定性代码** |
| `validate_credit_code(code)` | 统一社会信用代码 18 位含校验位 | **确定性代码** |
| `lookup_company(credit_code)` | 按信用代码补全法人全称 | 联网工具，替代 LLM 猜 |
| `render_complaint(fields)` | 结构化字段 → 起诉状（模板填槽） | 见"起诉状生成" |

### Agent 循环与 human-in-the-loop

- agent 在抽取节点可多轮：抽取 → 自校验（值回原文命中？）→ 命中失败置空 → 缺关键字段则 `search_in_docs` / `ocr_page` 定向补齐。
- 护栏：循环次数上限、成本上限、"连续两轮无进展 → 暂停问律师"。
- **interrupt 断点**：材料缺失、字段冲突、需联网确认名称时，agent 通过 LangGraph `interrupt` 暂停，把决策抛到前端审核页；律师给出选择后 `resume` 继续。Step 3 审核页即此断点的 UI 呈现，支持多次往返。

---

## API 路由设计（后端）

v2 以 agent 会话为中心。前端与后端通过"启动 + 恢复"两类接口交互；进展经 SSE/stream 推送。

```
POST /api/upload
  输入：multipart/form-data（多个文件）
  处理：parse_file 逐个解析 → 可解析 PDF 用 PyMuPDF → Word 用 python-docx
        扫描件此处不整份 OCR，仅标记 is_scanned，留待 agent 按需 ocr_page
  输出：{
    files: [{ filename, identified_type, text, is_scanned, page_count }],
    can_proceed, missing_materials, warnings
  }

POST /api/analyze            # 启动 agent，替代 v1 的 /api/extract
  输入：{ files: [...], internet_allowed: boolean }
  处理：启动 LangGraph 图；清点 → 抽取(工具循环) → 代码校验 → 计算 confidence
  输出（stream/SSE）：分步进展事件；结束时产出：
    {
      run_id,                        # 用于 resume
      state: CaseState,              # 含每字段 value/src/confidence/status
      pending: null | {              # 若命中 interrupt 断点
        kind: 'missing'|'conflict'|'confirm',
        question, options, field_keys
      }
    }

POST /api/resume             # 律师在断点处给出决定后恢复 agent
  输入：{ run_id, decisions: {...} }   # 冲突选值 / 补录 / 确认联网结果
  输出：同 /api/analyze（可能再次 pending，直至 pending=null）

POST /api/generate
  输入：{ validated_state: CaseState }
  处理：render_complaint（模板填槽，非 LLM 自由生成）
  输出：{ complaint_text }            # 或直接返回结构化字段供前端 docx-js 渲染
```

Word 文件仍默认在前端用 docx-js 按模板填槽生成（避免后端装 Node 依赖）；后端 docxtpl 为可选备选。

---

## 交叉校验（②：确定性代码，非 LLM）

移出 LLM，落到 `backend/app/services/validators.py`：

- **金额勾稽**：`total_amount == paid_amount + unpaid_amount`（浮点按分容差）
- **台数一致**：`elevator_qty_by_approval == elevator_qty_by_contract == elevator_qty_by_acceptance`
- **信用代码**：18 位、字符集、校验位
- **日期**：合法性、`acceptance_latest_date` 取各验收报告最晚合格日
- **名称**：分公司原文 vs 应填法人全称（差异→需律师/联网确认）

校验产出：每个字段的 `status` 结论（正常/冲突/缺失）+ 供展示的高亮说明文本。
`prompt-a-validate.md` **降级**为"仅生成给律师看的自然语言高亮说明"，不再承担一致性判断。

---

## 可信度评分（③：分层）

律师不信任黑盒总分。**硬信号定状态，软信号给 confidence，confidence 只服务 UI 优先级。**

**A. 确定性硬信号（是/否，直接决定 `FieldStatus`）**
- 来源锚定：`value` 能否在原文 `find()` 精确命中（否 → 判幻觉、置空）
- 格式校验：信用代码/日期/金额可解析
- 勾稽/一致性：金额、台数（见上）

**B. 软估计（加权成 0–100 confidence，仅排序/默认展开）**

| 信号 | 权重 | 取值 |
|---|---|---|
| 来源通道 | 40 | 文本=1.0 / OCR=0.6 / 多模态=0.7 |
| 锚定质量 | 30 | 精确命中=1.0 / 模糊=0.5 / 无=0 |
| 自一致性 | 20 | 两次或两模型一致=1.0 / 否=0 |
| 模型自评 | 10 | 模型输出的 confidence（弱信号） |

`confidence = Σ(权重 × 信号值)`，实现于 `backend/app/services/confidence.py`（纯确定性、可单测）。

**score → 状态映射**：

```
勾稽冲突 / 台数不一致        → ⚠️ 冲突（最高优先级）
value=null 或锚定失败        → ❌ 缺失
来源含 OCR/扫描 或 conf<60   → 🔍 待核实
其余                          → ✅ 正常
```

审核页用 confidence 排序、决定默认展开；数字放 hover，不喧宾夺主。
案件级另给"起诉状就绪度"（关键字段是否齐全且非冲突），比单一总分更贴合律师心智。

---

## 起诉状生成（④：模板填槽）

- LLM/agent 只产出**结构化字段**，不写全文。
- 用带占位符的模板（`complaint-template.md` 已是模板雏形）做**确定性渲染**：前端 docx-js 填槽（默认）或后端 docxtpl。
- 标注规则由**代码**按字段 status 贴，稳定可控：
  - `value=null` → 正文写 `【待补充】`
  - 冲突字段 → `【高亮冲突：<值>】`
  - OCR/扫描来源 → `⚠️ 待核实：<值>`
  - 正常 → 直接写入
- 金额同时输出汉字大写与阿拉伯数字；日期 `XXXX年XX月XX日`。

---

## 项目结构

```
legal-doc-app/
├── CLAUDE.md
├── prompts/                         # LLM Prompt 模板（纯文本）
│   ├── prompt-a-checklist.md        # 材料清点（JSON）
│   ├── prompt-a-extract.md          # 字段抽取（JSON，结构化输出）
│   ├── prompt-a-validate.md         # 仅生成高亮说明文本（不再做一致性判断）
│   ├── prompt-b-generate.md         # v1 遗留；模板渲染后仅作兜底/参考
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
│   │   │   └── preview/             # ComplaintPreview
│   │   ├── hooks/
│   │   │   ├── useFileUpload.ts
│   │   │   └── useComplaintFlow.ts  # 流程状态 + analyze/resume 往返
│   │   ├── lib/
│   │   │   ├── api.ts               # 后端 API 客户端（含 stream/resume）
│   │   │   ├── doc-generator.ts     # Word 模板填槽（docx-js）
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
│   │   │   └── generate.py          # POST /api/generate
│   │   ├── agent/
│   │   │   ├── graph.py             # LangGraph 状态图定义（节点/边/interrupt）
│   │   │   ├── state.py             # CaseState（Pydantic）
│   │   │   └── nodes.py             # 清点/抽取/校验/生成节点
│   │   ├── services/                # 工具层（框架无关，纯函数，可单测）
│   │   │   ├── file_parser.py       # PDF/Word 文本提取调度
│   │   │   ├── ocr_engine.py        # Qwen-VL-OCR（DashScope）；RapidOCR 备用注释
│   │   │   ├── llm_client.py        # DeepSeek 封装（OpenAI SDK，结构化输出）
│   │   │   ├── prompt_loader.py     # 读取 prompts/，注入变量
│   │   │   ├── validators.py        # ② 确定性交叉校验
│   │   │   ├── confidence.py        # ③ 可信度评分
│   │   │   ├── anchoring.py         # 值回原文命中/定位
│   │   │   └── company_lookup.py    # 联网企业信息查询
│   │   └── config.py
│   ├── requirements.txt
│   └── .env.example                 # DEEPSEEK_API_KEY, DASHSCOPE_API_KEY
│
└── docker-compose.yml               # （可选）一键启动
```

---

## 代码规范

### Codebase Management

- **Code Standards**：
  - 前端：TypeScript strict mode，不允许 `any`
  - 后端：Python 3.10+，type hints，Pydantic 做请求/响应与 CaseState 模型
- **Documentation**：每个文件顶部一行注释说明职责；复杂业务逻辑必须注释意图；变更时同步更新注释
- **Formatting**：
  - 前端：Prettier（`printWidth: 100`, `singleQuote: true`, `semi: true`）+ ESLint
  - 后端：Ruff（lint + format）
  - 导入顺序：标准库 → 第三方 → 项目内
- **Version Control**：提交粒度以功能点为单位，提交信息说明改动内容

### Feature Implementation

- **Leverage Existing Assets**：新功能先查 shadcn/ui 是否有现成组件；业务逻辑放 `lib/`（前端）或 `services/`（后端），不写在组件/路由里；**工具逻辑放 `services/`，编排逻辑放 `agent/`，二者不混**
- **Component Patterns**：函数组件 + hooks；状态提升到最近共同父组件；shadcn/ui 不手改源码
- **Naming**：
  - 前端：组件 PascalCase（`FieldTable.tsx`），工具 kebab-case（`field-map.ts`）
  - 后端：模块 snake_case（`ocr_engine.py`），类 PascalCase，函数 snake_case
  - 每个文件单一职责，组件不超过 200 行

### Error Handling

- LLM API：timeout 120s，失败重试 1 次，返回中文错误信息
- OCR：timeout 120s，失败返回 `{ error: "OCR 处理失败：具体原因" }`
- 文件解析：不支持格式返回明确错误，不静默失败
- JSON 解析：优先用结构化输出；仍需兜底去除 markdown 代码块标记后重试
- Agent：循环/成本超限 → 停止并暂停问律师，不静默烧钱
- 前端所有用户提示一律中文

### Documentation Best Practices

- **Location**：CLAUDE.md 在根目录；Prompt 在 `prompts/`；前后端文档各自目录内
- **Change Logs**：优先更新已有文件，不另建新文件

---

## 应用流程

### 用户视角（单页面 Stepper，共 4 步）

```
Step 1: 上传文件
  - 拖拽/点击上传，支持 .pdf / .docx
  - 联网查询开关（默认开启）
  - "开始分析" → POST /api/upload

Step 2: AI 处理中（agent 运行，SSE 进展）
  - 步骤条：清点材料 → 抽取字段 → 交叉校验
  - 每步摘要（"识别到3份文件""抽取到23个字段""发现2个冲突"）
  - agent 命中断点 → 直接进入 Step 3 的对应待决项
  - 出错 → 中文错误 + 重试 → POST /api/analyze

Step 3: 审核确认 ⭐ 核心页面（agent 断点的 UI）
  - 左侧：字段表格（字段名中文 | 值 | 来源出处 | 状态）
    - 状态：✅ 正常 / ❌ 缺失 / ⚠️ 冲突 / 🔍 待核实(OCR)
    - 按 confidence 排序，低分默认展开；数字放 hover
    - 冲突字段展开各来源值，律师选择
    - 所有字段可点击编辑
  - 右侧：校验报告 + 高亮列表
  - 律师决定 → POST /api/resume（可多次往返直至无待决）
  - 底部："确认并生成起诉状"

Step 4: 预览下载
  - 起诉状全文预览（标注保留）
  - "下载 Word"（docx-js 模板填槽）
  - "返回修改" → POST /api/generate
```

### 技术视角（数据流）

```
上传文件 → POST /api/upload
    ├── .docx → python-docx → text
    ├── .pdf 可解析 → PyMuPDF → text
    └── .pdf 扫描件 → 仅标记 is_scanned（不在此整份 OCR）
    │
    ▼
POST /api/analyze → 启动 LangGraph agent
    ├── 清点节点（Prompt A-1）→ 缺材料 → interrupt 问律师
    ├── 抽取节点（Prompt A-2，结构化输出）
    │     ├── 主取审批表结构字段
    │     ├── 缺条款 → search_in_docs / ocr_page 定向补（扫描合同按需）
    │     └── 自校验：值回原文命中？否 → 置空
    ├── 校验节点（validators.py，代码）→ 金额勾稽 / 台数三源 / 格式
    ├── confidence.py → 每字段 confidence + status
    └── 有冲突/需确认 → interrupt 问律师
    │
    ▼
前端渲染 CaseState → 中文字段表格；律师编辑/选择/确认
    │  （POST /api/resume 往返，直至 pending=null）
    ▼
POST /api/generate → render_complaint（模板填槽）→ 起诉状全文
    ▼
前端 docx-js 生成 Word → 下载
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
  options?: string[];          // 冲突时的候选值
  field_keys: string[];
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

OCR_TIMEOUT = 120  # 秒

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50

# Agent 护栏
AGENT_MAX_STEPS = 12          # 单次运行最大工具/循环步数
AGENT_MAX_OCR_PAGES = 20      # 单次运行最多按需 OCR 的页数（成本上限）

# 可信度阈值
CONFIDENCE_UNCERTAIN_BELOW = 60   # 低于此值标记为"待核实"

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
| `prompt-a-checklist.md` | `agent/nodes.py` via `prompt_loader.py` | 材料清点 | JSON |
| `prompt-a-extract.md` | `agent/nodes.py` via `prompt_loader.py` | 字段抽取 | JSON（结构化输出） |
| `prompt-a-validate.md` | `agent/nodes.py` via `prompt_loader.py` | 仅生成高亮说明文本 | 文本 |
| `prompt-b-generate.md` | v1 遗留；模板渲染后仅作兜底 | — | 文本 |
| `complaint-template.md` | `services/`（模板常量） | 模板 | — |

> 交叉校验的"判断"已移出 prompt，改由 `services/validators.py` 完成；`prompt-a-validate.md` 只产出面向律师的自然语言说明。

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

## 迁移路线（v1 流水线 → v2 agentic）

按增量推进，每步可独立验证，不一次性推翻：

1. **工具层先行**：把校验/评分/锚定实现为 `services/` 纯函数（`validators.py`、`confidence.py`、`anchoring.py`），配单测。此步不动流程，即可提升可靠性。
2. **抽取结构化**：`llm_client.py` 启用 function calling/结构化输出，`FieldValue` 加 `confidence/page/anchor`，抽取后做原文锚定与置空。
3. **校验移出 LLM**：`/api/extract` 拆分——LLM 抽取 + 代码校验；`prompt-a-validate.md` 降级为说明文本。
4. **模板渲染**：`/api/generate` 与前端 `doc-generator.ts` 改为模板填槽，标注由代码贴。
5. **引入 agent 编排**：`agent/graph.py` 用 LangGraph 串起节点，加入 `interrupt` 断点；`/api/extract` → `/api/analyze` + `/api/resume`；前端审核页支持多次往返。
6. **按需 OCR 与检索**：扫描合同不再整份 OCR，改 `ocr_page` + `search_in_docs` 定向补齐。
7. **联网工具**：`company_lookup.py` 接企业信息查询，替代 LLM 猜名称。

---

## 不包含（后续迭代预留）

- 数据库（PostgreSQL）— 案件持久化（agent checkpoint 届时落 DB）
- 用户认证 / 权限管理
- 多模态 LLM 直传扫描件（替代 OCR，进一步砍误差链）
- 批量案件处理
- Node.js 后端（Fastify）
- BullMQ 异步任务队列
- S3 / 云存储
- 起诉状版本对比
- 操作审计日志
- Docker 化部署
- **LangChain 本体**（仅采用 LangGraph 的图/检查点，不引入其余生态）
- 向量检索（v2 条款定位先用关键词；量大再上向量）
```
