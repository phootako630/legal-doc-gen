# CLAUDE.md — 安装合同纠纷起诉状自动化系统（v2 · Agentic）

本文件是 AI 编码助手（Codex / Claude / Copilot / Cursor）的项目指令文件，包含项目上下文、代码规范和开发约束。Codex 通过根目录 `AGENTS.md` 引用本文件。

## 中文法律领域与 skills 约定

- **默认语言**：需求澄清、skill 执行中的提问、方案讨论、代码评审、领域文档、issue 和 PR 默认使用简体中文。面向律师的界面、错误提示、审核说明和生成文书必须使用中文。
- **技术标识保留原文**：代码标识符、API 字段、命令、文件路径和已有 issue 标签不翻译、不因中文交流而改名。英文 skill 指令可直接使用，执行时按本项目语境解释。
- **中文术语为准**：领域建模以材料和项目中使用的中文法律术语为准；英文译名仅作辅助，不作为字段含义的依据。不得将“法定代表人”与“负责人”、“违约金”与“利息”、“检验合格日期”与“验收日期”等概念直接视为同义词。字段映射或业务归类必须保留原始称谓，说明依据；无法确定时交由律师确认。
- **证据原文保留**：主体名称、合同编号、付款条款、违约条款、争议解决条款和来源片段保留原文，不翻译成英文后再回译，不润色或静默纠正 OCR。需要格式归一化时保留原值与来源，并将归一化值与引用原文区分开。
- **法律语境明确**：通用 skills 不得默认套用其他法域的诉讼流程或法律概念。涉及法律适用、管辖、请求权或计算规则的变化，需明确依据与适用时间；不得凭 skill 中的示例推导法律结论，最终判断仍由律师确认。
- **验证覆盖中文材料**：修改解析、OCR、抽取、来源锚定、金额日期处理或文书渲染时，使用与改动相关的中文样例验证；重点关注中文标点、全角字符、换行、扫描识别错误及金额单位。测试材料采用合成或脱敏数据。
- **开发指令与运行时分开**：本文件约束编码助手；应用中的模型行为由实际代码和 `prompts/` 决定。修改本文件不能替代运行时 prompt 修改或功能验证。

> **v1 → v2 变更提示**：v1 是"固定流水线"（`upload → 清点 → 抽取 → 校验 → 生成`，控制流写死）。v2 将核心处理改为 **agentic**：由一个带工具的 agent 驱动控制流，按需读取材料、自校验、在关键节点暂停问律师。本文件描述的是 v2 目标架构；标注「v1 遗留」的段落为迁移前的旧实现，逐步替换。

---

## 项目概述

面向律师团队的 Web 应用，用于自动化处理安装合同纠纷案件的民事起诉状准备工作。律师上传案件文件，agent 自动完成材料清点、信息抽取、交叉校验，律师确认后生成起诉状 Word 文档。

**核心用户**：中国大陆律师团队，不懂技术，不看 JSON，不看英文。所有面向用户的内容必须是中文。

**目标**：验证 AI 抽取准确率和律师使用体验。支持可解析 PDF、扫描件 PDF（OCR）和 Word 文件。登录：账号由管理员在服务器上用 `scripts/manage_users.py` 创建（无注册入口），会话 Cookie 7 天免登录，律师可自行修改密码；部署时强制开启（`AUTH_ENABLED=1`），本地开发与单测默认关闭；案件状态（agent checkpoint、上传的扫描件）落盘到 `backend/data/`（SQLite + 文件），服务重启后仍可 resume，超过 `CASE_RETENTION_DAYS`（默认 7 天）自动清除（服务启动即清、之后每小时清，resume / 读取时也拒绝过期数据）；落盘文件仅属主可读写（目录 0700、文件 0600）；不引入业务数据库。

---

## 核心原则

- **Reusability**：优先复用已有组件和工具函数，不重复造轮子
- **Stability**：任何改动不得破坏现有功能，改动前先理解上下文
- **Quality Assurance**：所有新功能必须有完整的类型定义，关键逻辑需有注释说明意图
- **AI 不做最终判断**：agent 只负责抽取、校验、提议，律师负责确认和修改；关键分歧点 agent 必须暂停问律师（human-in-the-loop），不得自行拍板
- **出处可追溯**：每个字段值必须标注来源文件 + 位置（页码/锚点），且值必须能在原文精确命中
- **宁缺勿造**：无法确定的字段标为缺失（value=null），绝不编造；抽取值回原文比对失败一律标「待核实」（来源注明「原文未找到」，审核页与起诉状都提醒律师），不当作正常值写入文书
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
OCR：          Qwen 多模态云端 API（阿里云 DashScope，默认 qwen3.8-flash；免费额度 key + 付费备用 key）；备用 RapidOCR（本地 ONNX，代码注释保留）
LLM 调用：     OpenAI SDK（兼容 DeepSeek API），启用 function calling / 结构化输出；DeepSeek 不可用时改用通义 qwen3.8-flash（DashScope 兼容接口）
模板渲染：     docxtpl（可选，后端渲染）或前端 docx-js（默认，见 API 设计）
企业信息查询： 联网工具（企查查/天眼查/公示系统 API，按信用代码补全法人全称）
运行方式：     uvicorn，单进程，本地运行
```

### 关于 agent 框架的取舍（①）

- **不引入 LangChain 本体**：抽象层深、prompt 不透明、版本易 breaking，对"可审计"要求高的法律场景是负担。结构化输出、重试等零件自行实现或用轻量库。
- **采用 LangGraph 的图 + checkpoint + interrupt**：正好承载"抽取→发现冲突→定位重读→暂停问律师→恢复"的控制流。checkpoint 存 SQLite（`agent/checkpoint.py`）；未来接 PostgreSQL 时替换 saver 即可，零重构。
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
| **审批表** | KA 大客户收款不良合同诉讼审批表 | 可解析文本、**键值表单** | ⭐⭐⭐ 最高（结构化，逐项直取） | **主数据源**：合同类型、合同分公司、被告名称、联系人、合同号、台数（签约/实际）、合同总额、已付/未付、最后付款日等字段直接取此（被告住址不取此，见起诉状生成） |
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
│  边：条件跳转（有冲突 / 需确认 → 暂停问律师）                    │
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

- agent 在抽取节点可多轮：抽取 → 自校验（值回原文命中？）→ 命中失败标待核实 → 缺关键字段则 `search_in_docs` / `ocr_page` 定向补齐。
- 护栏：成本上限（全案 OCR 总页数 `AGENT_MAX_TOTAL_OCR_PAGES`，用完时在出处注明可能未读到）。
- **材料清点**：按上传页律师确认的文件类型，由代码判定齐全与否（`services/materials.py`，与上传接口、前端 `lib/materials.ts` 同一口径），不调 LLM。缺材料不终止、不暂停：律师在上传页已看到缺什么，点了「材料不全，仍然分析」就照常继续，缺的字段留【待补充】，校验面板琥珀色提醒（`materials` 检查）。
- **interrupt 断点**：字段冲突、需联网确认名称时，agent 通过 LangGraph `interrupt` 暂停，把决策抛到前端审核页；律师给出选择后 `resume` 继续。Step 3 审核页即此断点的 UI 呈现，支持多次往返。冲突断点附各字段的候选值与来源（`pending.field_options`，`services/conflict_options.py`）：金额勾稽给出三项现值及用另两项推出的值（有一项含「万 / 亿」等单位、不是纯数字写法时不推算），写进起诉状的台数给出各来源的台数及出处（各来源字段只给自身现值，不跨来源补），信用代码 / 日期给出现值与出处；律师点选或自填。断点节点内不放 OCR / LLM 等昂贵或有副作用的操作：resume 时 LangGraph 会从头重跑被打断的节点（`contract_scan` 与 `qty_check` 分开即为此）。

---

## API 路由设计（后端）

v2 以 agent 会话为中心。前端与后端通过"启动 + 恢复"两类接口交互；进展经 SSE/stream 推送。

```
POST /api/upload
  输入：multipart/form-data（多个文件）
  处理：parse_file 逐个解析 → 可解析 PDF 用 PyMuPDF → Word 用 python-docx
        扫描件此处不整份 OCR，仅标记 is_scanned，留待 agent 按需 ocr_page；文件名认不出类型的扫描件
        只 OCR 第 1 页、按标题再认一次（`file_parser._identify_scanned_by_title`，失败仍为「未知」，文字不保留）；
        半数以上页没有文字层的 PDF（合同正文扫描、附件电子版）也按扫描件处理，有文字层的页先放进 pages，
        按需 OCR 时直接用、不占页数预算（`SCANNED_PAGE_TEXT_THRESHOLD`）
  输出：{
    files: [{ filename, identified_type, text, is_scanned, page_count }],
    can_proceed, missing_materials, warnings
  }

POST /api/analyze            # 启动 agent，替代 v1 的 /api/extract
  请求头：Idempotency-Key（可选，前端每次上传生成一个）——同一编号只分析一次：
        正在跑 → 接上同一个任务；已跑完 → 直接返回该会话当前状态；失败 → 不记，重试重新分析；
        编号相同但材料不同 → 409。编号→会话存 SQLite（analyze_keys），随会话过期清除
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

GET /api/case/{run_id}       # 取回会话当前状态（刷新页面后「继续上次的案件」）；过期 / 不存在 404
#   按账号隔离：会话记下发起分析的登录账号（run_meta.owner），case / resume / analyze/result / feedback / generate
#   的改动统计只认本人，别人的会话一律按不存在（404）处理；未开启登录不限制，升级前的旧会话（无 owner）不限制
GET /api/analyze/result?key=<请求编号>  # 处理页刷新后接回同一次分析：还在跑就等它跑完，已跑完直接返回；失败 / 重启 / 过期 404
GET /healthz、GET /readyz（不在 /api 下，nginx 不转发）  # 存活（docker 健康检查）/ 就绪（checkpoint 库读写、磁盘余量 `READY_MIN_FREE_MB`、Key 已配置；deploy.sh 用，不就绪回滚）
POST /drain、GET /busy（不在 /api 下，nginx 不转发）  # deploy.sh 重启前先 drain（新的分析 / 恢复 503「系统正在更新」），再等 busy 归零（含编号落盘）

POST /api/generate
  输入：{ validated_state: CaseState }
  处理：render_complaint（模板填槽，非 LLM 自由生成）
  输出：{ complaint_text }            # 或直接返回结构化字段供前端 docx-js 渲染

# 登录（AUTH_ENABLED=1 时，除下面三个外所有 /api 未登录一律 401 + 响应头 X-Login-Required: 1）
POST /api/auth/login               # 账号 + 密码 → 设置 HttpOnly / SameSite=Strict 会话 Cookie（HTTPS 下加 Secure）
POST /api/auth/logout              # 注销当前会话
GET  /api/auth/me                  # { auth_enabled, username }，前端据此决定是否显示登录页
POST /api/auth/change-password     # 需登录；本机会话保留，其他会话失效
#   同一 IP 对同一账号连续输错 5 次 / 同一 IP 累计 20 次 → 锁 15 分钟（services/login_guard.py，进程内存）

# 原告信息表（查看 / 下载不限；标 🔒 的须请求头 X-Admin-Token = 管理员口令）
GET  /api/branches                 # 当前表 + 更新时间 + 是否过期
GET  /api/branches/export          # 下载 .xlsx
GET  /api/branches/history         # 历史版本列表
POST /api/branches/admin-check  🔒 # 核对口令
POST /api/branches/preview      🔒 # 上传 Excel → 解析 / 校验 / 变更比对（不保存）
POST /api/branches              🔒 # 确认保存（旧版本存档）
POST /api/branches/restore      🔒 # 恢复历史版本
```

Word 文件仍默认在前端用 docx-js 按模板填槽生成（避免后端装 Node 依赖）；后端 docxtpl 为可选备选。

---

## 交叉校验（②：确定性代码，非 LLM）

移出 LLM，落到 `backend/app/services/validators.py`：

- **金额勾稽**：`total_amount == paid_amount + unpaid_amount`（浮点按分容差）
- **台数一致**：`elevator_qty_by_approval == elevator_qty_by_contract == elevator_qty_by_acceptance`
- **信用代码**：18 位、字符集、校验位
- **日期**：合法性、`acceptance_latest_date` 取各验收报告最晚合格日
- **名称**：不做分公司核实。原告按规则拼接（见起诉状生成）；被告是分公司时只起诉分公司，不列总公司（律师确认）
- **台数**：有验收报告台数时以其为准、不暂停；只有审批表与合同一致而报告不同时标待核实。无验收报告台数时三源不一致仍暂停问律师

校验产出：每个字段的 `status` 结论（正常/冲突/缺失）+ 供展示的高亮说明文本。
通过但需律师留意的检查（如台数不一致、已按规则取验收报告口径）带 `needs_review`，校验面板标琥珀色，不算冲突。金额在说明中一律写到分（千分位 + 两位小数）。
`prompt-a-validate.md` **降级**为"仅生成给律师看的自然语言高亮说明"，不再承担一致性判断。

---

## 可信度评分（③：分层）

律师不信任黑盒总分。**硬信号定状态，软信号给 confidence，confidence 只服务 UI 优先级。**

**A. 确定性硬信号（是/否，直接决定 `FieldStatus`）**
- 来源锚定：`value` 能否在原文逐页命中（`extraction.enrich_provenance`）。否 → src 追加「（原文未找到，待核实）」；命中的文件与模型标注的不同 → src 改为实际命中的文件，与页码一致。抽取后与 OCR 补读后各锚定一次，标记每次重算
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
value=null                    → ❌ 缺失
锚定失败（原文未找到）        → 🔍 待核实
来源含 OCR/扫描 或 conf<60   → 🔍 待核实
其余                          → ✅ 正常
```

审核页用 confidence 排序、决定默认展开；数字放 hover，不喧宾夺主。
案件级另给"起诉状就绪度"，比单一总分更贴合律师心智：按起诉状模板的实际填空统计（措辞类占位符不计），有值且不冲突的占比（`complaint_renderer.fill_status`），与预览页「N 处待补充」口径一致。只有 100% 才显示绿色，还有待补充时显示琥珀色。

---

## 起诉状生成（④：模板填槽）

- LLM/agent 只产出**结构化字段**，不写全文。
- 用带占位符的模板（`complaint-template.md`，按律师提供的诉状模板逐句对齐）做**确定性渲染**：后端 `complaint_renderer.py` 填槽，前端导出 Word。
- 标注规则由**代码**按字段 status 贴，稳定可控：
  - `value=null` → 正文写 `【待补充】`
  - 冲突字段 → `【高亮冲突：<值>】`
  - OCR/扫描来源 → `⚠️ 待核实：<值>`
  - 正常 → 直接写入
- 格式对齐律师模板：金额只写阿拉伯数字（模板自带 `¥…元`），日期 `2024年2月26日`（不补零）；已付 / 欠款默认写占合同款比例，二者之和不等于总价时写算式（见下）。
- 可选占位符（`complaint_renderer._OPTIONAL_KEYS`：违约条款引用、已付 / 欠款括注、违约金上限、质保期到期句、补充协议句）：规则判定不需要时值为空串，渲染为空、不算【待补充】、不计入就绪度。
- 导出 Word 保留黄色高亮：`/api/generate` 把每个填空值包在 `⟦…⟧` 里（`render_complaint(mark_fills=True)`），前端预览标浅黄、Word 导出转为黄色突出显示，供律师最后复核。
- **律师确认的填写规则**（两份《起诉状规则确认单》，确定性代码实现于 `services/derived_fields.py`，律师改过的值不覆盖）：
  - 原告：买卖合同 = 总公司全称（`PLAINTIFF_HQ_NAME`），与合同盖章页乙方不一致时以乙方为准并标待核实；安装合同一律以合同盖章页「乙方 / 安装方 / 安装单位」（`contract_party_b`）为准，审批表「合同分公司」只作参考（确认单（五）第 1 题：有的诉讼由二、三级分公司提起，合同却是一级分公司签的）；合同乙方读不到时才按审批表推出（路径中第一个分公司拼总公司全称；路径里是独立工程 / 营销公司时原告就是该公司，按原告信息表补全称），标待核实。合同乙方不含总公司全称、原告信息表也查不到的（如验收报告上的施工单位）不当原告；第三行：分公司写「负责人」，总公司和独立公司写「法定代表人」；只有总公司在姓名后加职务（`PLAINTIFF_HQ_REP_TITLE`，「张三，董事长」），信息表已写了职务（「姓名，职务」「姓名、职务」「姓名（职务）」或空格后的常见职务名）的保留原写法、不再追加，职务按全称比较（「副董事长」≠「董事长」），不同的标待核实；分不清写法的（如「網谷 憲晴」「张三 监事」）保留原文、不追加、标待核实；电话固定 `PLAINTIFF_PHONE`；信用代码 / 负责人 / 住址取原告信息表（见下「原告信息表」）
  - 合同类型（审批表「合同类型」）切换措辞：买卖合同「安装」→「供货」、「安装价格」→「产品价格」
  - 台数：「约定原告负责安装 N 台」取合同台数（`elevator_qty_contract`），「N 台均于……验收合格」取验收报告台数（`elevator_qty`，代码计数：文字版报告按不同设备代码、取不到按报告编号；扫描报告按首页，见下「验收日期」）。合同与报告不一致时，`contract_scan` 节点补读合同设备清单、数 VGE 型号（家用电梯，无需验收报告，`services/equipment_list.py`）：报告 = 合同 − VGE → 继续并提醒家用电梯；否则 `qty_check` 节点暂停，提醒律师核对报告是否齐全 / 有无补充协议 / 是否口头取消部分电梯，由律师定台数
  - 签约日期：合同盖章页日期（盖章处写成「2024-05-24」的也算）；盖章页没写日期时取封面写到年月日的「签订日期」（只写到年月的「合同订立时间」不取）；扫描合同未识别时暂取审批表「签约时间」
  - 被告名称：以合同盖章页「甲方 / 买方 / 需方 / 发包方」（`contract_party_a`）为准，审批表「合同买方名称」只在合同甲方读不到时用（律师反馈：审批表有时多写括号备注；不一致标待核实）。合同当事人只取合同，src 指向验收报告 / 审批表的 `contract_party_a/b` 由 `apply_rule_guards` 清空
  - 被告住址 / 法定代表人：一律用工商登记信息（企查查、爱企查、启信宝），审批表地址不用（`extraction.apply_rule_guards` 清空）；审批表买方地址 / 合同盖章页甲方地址存为 `defendant_address_ref`，不写进起诉状，只在约定被告所在地（或未约定地点）管辖、工商住址还没填时推断法院，管辖句写明「【系统按被告地址「…」推断，待核实】」
  - 联系人：只取审批表「联系人」，不取「甲方收款联系人」
  - 验收日期：检验报告的批准 / 盖章日期，多份取最晚一批（文字版报告由代码在全部报告文件里取「批准」后的最晚日期，`extraction.apply_acceptance_date`，读不到才用 LLM 的值；一个案件常是每台电梯一份 PDF）。报告是扫描件（常为手机拍照）时，还有没读过的扫描报告 `report_ocr` 节点就逐页 OCR（上限 `AGENT_MAX_REPORT_OCR_PAGES`），按全部报告汇总（文本报告与扫描报告的批准日期取最晚，台数 = 文本报告计数 + 扫描报告首页计数；预算用尽或有页失败时出处注明「可能不全」，扫描计数的 OCR 待核实提示带到起诉状台数），用代码取「批准」后的日期、第一份「安装地点」，并按报告首页（「设备品种」栏）数台数——照片常裁掉报告编号，按编号去重会少数；同一设备代码 / 报告编号的首页只算一份（合并版与单份报告同时上传不重复计数，`acceptance_report.report_first_page_ids`；读不到标识的首页照算，律师新案例：中交一公局）；某份扫描报告数不出首页时退回按设备代码 / 报告编号计数，仍数不出则出处注明「可能不全」（`nodes._merge_acceptance_qty`、`services/acceptance_report.py`；OCR 输出的 markdown / HTML 表格先统一成纯文本）；仍读不到时退回审批表「政府验收时间」，标待核实
  - 付款条款：原文（`payment_clause_text`）+ AI 归纳（`payment_clause_summary`，标「AI 归纳，待核实」）。起诉状只引用欠款对应的付款节点，条款号写到该项，照抄原文、加引号（确认单（五）第 3 题选 A）：`services/payment_nodes.py` 按编号拆节点（付款条款写成表格时按行拆：OCR 的 Markdown / HTML 表格，表头「累计」列按累计比例、「比例」列按本期比例，序号列作条款编号，其余单元格照抄；「合计 / 总计」汇总行、违约金行、没写支付的保函行不计），比例只认「支付……X%」（本期）与「支付至 / 付至……X%」（累计），保函百分比、「付到85%时」抵扣条件不计；「已付 + 欠款」占合同总价的比例对上某个累计节点时，引用已付比例到该比例之间的节点（`payment_cite_location`「第7.6条、第7.8条」，一级编号挂条款位置「第二十八章第1项、第2项」；`payment_cite_text`，去掉「验收款：」这类小标题，待核实）；节点拆不出、对不上时退回原条款位置 + AI 归纳句（没有归纳时写原文）
  - 支付至比例（`payable_ratio`）：先用「已付 + 欠款」占合同总价的比例匹配付款节点累计比例（律师反馈：如付至 95%），对上即取该比例、`retention` 节点不再暂停；都对不上（如算出 85%、节点为 5%-10%-70%-95%-100%）留【待补充】，出处与校验面板（`payment_progress`，琥珀色）提醒律师：可能调过价、实际台数与合同不同或质保金到期时间不统一，请手动核对；节点拆不出时按质保金规则（见下）
  - 诉请金额 = 审批表「未付款金额」。旧版 KA 审批表（合同类型「买卖附带安装合同」）一张表同时列买卖、安装两份合同：按本案合同文件名 / 标题（供货 / 买卖 / 设备 / 采购 → 买卖，安装 → 安装）取对应一栏的合同总额 / 已付 / 未付 / 未付质保金，合同类型改为本案那一种；判断不了时不动（`extraction.apply_dual_contract_amounts`，律师新案例：徐州九里苏宁）。质保金：合同有质保金时 `retention` 节点暂停，附质保金条款原文与审批表未付质保金，按分期比例给出选项（如 5% → 100% / 95%；满一年 2%、满二年 3% → 100% / 97% / 95%），律师选定「支付至 X% 合同款」；条款写的只是乙方另开银行质保保函（须正向写明「乙方……开具 / 提供……保函」，且无否定保函、无「保函后支付」、无「质保金」/ 预留 / 扣留 / 返还等现金质保写法，`derived_fields.retention_is_guarantee`；拿不准仍问律师）时不暂停，按支付至 100%（待核实）
  - 移交：合同约定移交（甲方指定的）物业 / 物业单位的写「已全部移交物业」，否则写「已全部移交被告」（确认单（五）第 6 题，`handover_clause_text` / 付款条款）；合同付款条件含「结算」才加「并办理结算」（`handover_text`）
  - 应付金额：「被告应按合同约定支付至X%合同款¥…元」写出金额 = 已付 + 欠款（`payable_amount`，确认单（五）第 5 题），支付至的比例 × 合同总价与之不等时标待核实
  - 质保期到期句：欠款含质保金（审批表未付质保金 > 0）且质保期已届满时，在移交句后写「，质保期已于X年X月X日到期」（`warranty_expiry_sentence`，可选占位符），到期日 = 最晚验收合格日 + 质保期，标待核实；质保期不自验收起算、读不到或尚未届满时不写，出处说明原因（确认单（五）第 7 题）
  - 战略 / 框架合同：文件名或标题含「战略合同 / 战略合作 / 战略协议 / 框架合同 / 框架协议」的单独归为「战略合同」类（先于「补充协议」判断：《战略协议补充协议七》是战略合同的补充，仲裁条款在里面）；扫描件按需 OCR 另给 `AGENT_MAX_STRATEGIC_OCR_PAGES` 页，不被主合同用完的 50 页挤掉（律师反馈：徐州苏宁的争议条款在战略合同里），不当主合同；按需 OCR 找条款时排在主合同之后（`nodes._clause_ocr_targets`），且只补缺失的付款 / 违约 / 争议 / 质保条款（`nodes._STRATEGIC_CLAUSE_KEYS`），不取当事人、签约日期、合同名称、台数、工程地点等本案字段，也不以它的签章页为准；主合同争议条款只写「争议的解决……按战略合同执行」、本身没约定仲裁 / 法院时（`extraction.refers_to_strategic`），清空条款内容、保留主合同条款位置，再去战略合同里读（文字版不 OCR），读到后位置写「第X条及战略合同第Y条」，起诉状为「依据案涉合同第X条及战略合同第Y条约定，……」；取自战略合同的当事人、日期等本案字段由 `extraction.apply_strategic_guards` 清空
  - 补充协议：文件名含「补充协议 / 补协 / 修改单 / 变更协议」（或正文开头有「补充协议」）的材料单独归为「补充协议」类，不当合同 OCR（`file_parser._TYPE_KEYWORDS`）；有补充协议时「合同总价为¥…」写合同正文原价（`contract_total_in_contract` → `contract_total_stated`），其后加「后双方签订补充协议，确认合同总价变更为¥<审批表合同总额>元。」（`supplement_sentence`，可选占位符），一律标待核实交律师确认（确认单（五）第 8 题：补充协议可能改单价、数量、规格、税率）
  - 诉请「剩余合同款 / 到期合同款」：已付 + 欠款 = 合同总价写「剩余合同款」，否则写「到期合同款」；已付 + 欠款大于总价（可能调过价）或金额缺失时标待核实（`debt_term`，确认单（五）第 9 题）
  - 逾期利息：合同有甲方逾期付款利率 → 用约定（待核实）；否则「全国银行间同业拆借中心公布的一年期贷款市场报价利率」，一律自起诉之日起算，基数为欠款金额
  - 违约金：合同甲方逾期付款条款写的是「违约金」（`breach_clause_text` / `breach_interest_rate_text` 含「违约金」）→ 诉请与事实理由写「违约金」（`interest_term`），并在违约段落前引用条款「依据合同第X条约定，<原文>。」（`breach_clause_sentence`，待核实；条款号只写最细一级，如「第十条第10.2条」→「第10.2条」，原文中的甲方 / 乙方换成被告 / 原告，确认单（五）第 13 题）；否则写「逾期付款利息」、不引用。安装 / 买卖合同一样。约定违约金却没有按日 / 月 / 年计算的标准（须同时有比率或金额与计算周期，周期含「每日 / 每逾期一日 / 按日 / 日万分之 / 100元/日」等写法；「逾期超过30日」只是触发期限；「一次性结清」等付款方式字样不改变按期计算）时，按固定金额 / 一次性比例处理（律师确认单（四）第 1 题）：诉请与事实理由的「（以¥…为基数……计至实际付清之日止）」整段换成违约金金额（`penalty_amount`，`complaint_renderer` 替换模板），条款只有一个金额的照抄，写「合同总价 / 欠款的 X%」的按合同总价或未付金额算出，一律标待核实；含上限 / 下限 / 累计等附加条件、出现多个计算标准（按「基数 + 比例」比较，如「欠款的5%」与「合同总价的5%」算两档）或多个金额（分档）、比例与金额混用、比例基数识别不了、缺基数时不自动计算，留【待补充】；「每日100元」这类按期计付的固定金额不算一次性金额；律师改过计算标准则不切换。按合同费率计付且条款约定了上限（「最高不超过安装费总额的10%」）时，诉请与事实理由括号末尾照写上限（`penalty_cap`，可选占位符，待核实；金额千分位逗号不断句，写到哪分不清时留【待补充】）
  - 按日计付的固定金额违约金（补充答复 Q6）：诉请写「支付违约金¥X元（自A起，按照每日N元计至实际付清之日止，暂计至B）」（`complaint_renderer._DAILY_CALC`）。A（`penalty_start_date`）= 付款期限届满的次日：欠款是质保金（`retention_unpaid_amount` = 未付金额）→ 最晚验收合格日 + 质保期（`warranty_period`）+ 质保金返还期限（`retention_clause_text`「质保期满后 N 日内返还」，「质保期满后无息返还」为 0）的次日，质保期不自验收起算、没写返还条件、分期返还、带结算等前置条件、整条质保金条款里除质保期本身（「质保期24个月」）和紧跟「质保期满」的已解析期限外还有时间字样（如「一年内」「返还应在30日内完成」）时不推算（不按零天）；结算 / 确认 / 审批等前置条件在整条条款上检查（写在「质保期满」前面也算），只剔除「自竣工验收合格之日起」这类质保期起算写法；否则付款条款须只有一个付款节点且为「验收合格后 N 日 / N 个月内」、无移交 / 结算 / 发票等前置条件 → 最晚验收合格日 + N 的次日；按工作日计、多个付款节点（分号、句号或逗号连接的多期付款均算）、缺验收日期等推不出时留【待补充】。B（`penalty_until_date`）= 生成起诉状当日。X = 每日金额 × A 至 B 天数（头尾都算），三项取最终审核字段（律师改了每日标准 / 起算日 / 暂计日，金额按律师的值重算）；A、X 标待核实。按月 / 年计的固定金额仍留【待补充】
  - 已付 / 欠款：已付 + 欠款 = 合同总价时默认不写占合同款比例，由律师自行决定（确认单（五）第 4 题）；不等 → 欠款后写算式「（到期金额-已付款）」，到期金额 = 已付 + 欠款（待核实）（`paid_note` / `unpaid_note`）
  - 争议条款提到工程所在地 / 签订地 / 履行地 / 交货地点时，位置同时引用约定该地点的条款
  - 工程所在地 / 交货地点管辖：地点写到区县的直接采纳；只写了项目名的，按验收报告安装地点（`install_address`）推断并标待核实；仍推不出留【待补充】。约定交货地点法院时只取合同约定的 `delivery_place`，缺失时不拿工程地点充当（管辖句写「因交货地点为【待补充】」，法院按安装地点推断并标待核实）。法院按安装地点推断时，管辖句里直接写明「【系统按验收报告安装地点「…」推断，待核实】」，律师改过法院则不加
  - 管辖句 `jurisdiction_text` 按争议条款生成（一律待核实）：工程所在地 → 模板原句；原告 / 被告所在地 → 「故原告向 XX 人民法院提起诉讼」（XX 为该方住所地法院辖区）；未约定地点 → 「依据《中华人民共和国民事诉讼法》第二十四条，故原告向（被告住所地）XX 人民法院提起诉讼」；合同签订地 → 取合同盖章页「合同签订地」（`contract_sign_place`，没有时取封面「合同订立地点」`contract_sign_place_cover`；两处推出的法院不同时交律师判断，确认单（五）第 15 题）推断法院，管辖句「合同签订地为……，故原告向 XX 人民法院提起诉讼」，签订地没写到区县或未识别时留待补充；点名具体基层法院（「提交肥城市人民法院诉讼」）→ 直接采用该法院（待核实，律师新案例：山东裕景买卖），截不出法院名或点名中级法院 → 留待补充交律师；不考虑中级法院。争议条款写「合同施工安装地 / 安装地 / 施工地」法院按工程所在地处理（律师新案例：山东裕景安装）。法院名称：县级市、县只写县级市 / 县（「昆山市人民法院」「沛县人民法院」），市辖区写「市 + 区」（「南京市雨花台区人民法院」），地址省了「市」字的「苏州吴江区……」按「苏州市吴江区」推定（只认两字城市 + 两字区名；三四字城市、三字区名、开发区 / 园区等功能区分不清边界，不推）；只写县级市名的（「昆山……项目」「苏州昆山开发区……」）直接推定该县级市（县级市法院不分区，确认单（六）B；名单 `_COUNTY_LEVEL_CITIES` 只收江苏、湖南，名字后紧跟「区 / 县」的是同名区县（「怀化市洪江区」照常按市辖区）；名字后没写「市」、到第一个路 / 街 / 道之前又没有区 / 镇 / 项目等地名单位的是路名（「常熟路」「浏阳河大道」），不推）——`derive_court_district`，律师确认单（四）第 3 题、补充答复 Q5
  - 约定仲裁 → 整篇改为仲裁申请书（`document_kind`）：争议条款含「仲裁」，或争议条款没写法院、仲裁机构写在合同附录里（`arbitration_institution` 为「…仲裁委员会 / 仲裁院 / 仲裁中心」，律师新案例：徐州九里苏宁安装分包合同专用条件第 19 条 + 附录一）都算；模板固定文字替换 民事起诉状→仲裁申请书、原告→申请人、被告→被申请人、诉讼请求→仲裁请求、诉讼费用→仲裁费用、提起诉讼→提请仲裁、自起诉之日起→自申请仲裁之日起、诉至贵院→提请贵委仲裁、祈判如所请→祈裁如所请、贵院→贵委、判令/判决→裁决；管辖句「故申请人向 XX 仲裁委员会提请仲裁」，致送合同约定的仲裁机构（`addressee`）
  - 不附付款进度表（由办案律师自行决定）
  - 派生值随依据刷新：依据变了（如律师更换原告）而规则推不出新值时，清除旧推定值留【待补充】，不残留旧原告的信用代码 / 负责人 / 住址；派生的合同台数沿用合同台数的出处（页码、OCR 待核实标记）
  - 按需 OCR 除付款 / 争议条款外，签约日期还来自审批表或合同乙方未读到时，也以合同签章页为目标（「以下无正文」「签订日期：」「甲方（盖章）」），找到才停；欠款是质保金且违约金可能按每日固定金额计、质保期还没读到时，也以质保期为目标（`derived_fields.needs_warranty_period`）

### 落实律师答复时的改动点

律师确认单的答复变成规则时，按此清单逐项检查，避免漏改：

1. **规则代码**：`backend/app/services/derived_fields.py`（派生字段、措辞切换；律师改过的值不覆盖，自身来源前缀写「按律师规则：」）；涉及抽取的改 `services/extraction.py` 的 `apply_rule_guards`
2. **模板**：`prompts/complaint-template.md`（新占位符），仲裁措辞改 `complaint_renderer.py` 的 `_ARBITRATION_TERMS`
3. **抽取提示词**：`prompts/prompt-a-extract.md` / `prompt-a-clauses.md`（新字段、取值说明）
4. **需要律师拍板的**：在 `agent/nodes.py` 加 interrupt 断点（参照 `qty_check_node` / `retention_node`）
5. **前端**：`lib/field-map.ts`（中文名 + `reviewFieldKeys`）、`lib/types.ts`（`ExtractedFields`）
6. **测试**：`backend/tests/test_derived_fields.py` 等，用虚构数据
7. **文档**：本文件「律师确认的填写规则」、核心类型定义、字段名映射

### 原告信息表（全所共用底表，管理员维护）

- 原告的统一社会信用代码、法定代表人 / 负责人、住所地不在案件材料里，取自律师维护的《原告信息表》（Excel：原告名称 | 统一社会信用代码 | 住所地 | 法定代表人\负责人 | 联系方式）。它是全所共用的底表，**不是**每个案件上传的材料。
- 前端页头「原告信息表」入口：所有人可查看、下载当前 Excel；**只有管理员**（输入服务器 `ADMIN_TOKEN` 口令）能上传新版本或恢复历史版本。系统暂无账号体系，口令经请求头 `X-Admin-Token`（encodeURIComponent 编码）传给后端比对；未配置 `ADMIN_TOKEN` 时任何人都不能更新。保存 / 恢复时把当前登录账号（`request.state.user`）记为该版本的上传人（`uploaded_by`），页面与历史列表显示；未开启登录（本地开发）则为空。只想让某位律师上传：把口令只告诉这位律师。
- 更新分两步：`POST /api/branches/preview` 解析 + 校验 + 与当前版本比对（不保存）→ 管理员确认后 `POST /api/branches` 保存。完全相同的重复行自动合并并提醒；同名但内容不同 / 缺名称阻止保存；信用代码校验位不符、缺负责人或住址只提醒。
- 存储：`BRANCH_INFO_PATH`（默认 `backend/data/branch_info.json`），每次更新前旧版本存入 `BRANCH_HISTORY_DIR`，可 `POST /api/branches/restore` 回退。数据留在部署服务器，不进 git。
- 名称比对前去空白、半角括号转全角（律师表里部分行写「日立电梯(中国)」）。查询按文件修改时间缓存，上传后立即生效、无需重启。
- 字段来源写「《原告信息表》YYYY-MM-DD 版」；超过 `BRANCH_TABLE_STALE_DAYS`（90 天）未更新或尚未上传时，上传页提醒负责人可能已变更。

---

## 项目结构

```
legal-doc-app/
├── CLAUDE.md
├── .claude/                         # Claude Code 辅助：启动脚本 + skills（见「本地开发启动」）
├── prompts/                         # LLM Prompt 模板（纯文本）
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
│   │   │   ├── preview/             # ComplaintPreview
│   │   │   ├── branches/            # 原告信息表页面（查看 / 管理员上传 / 历史回退）
│   │   │   └── auth/                # 登录页、修改密码弹窗、页头账号菜单
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
│   │   │   ├── run_log.py           # 运行日志（JSONL：节点耗时/断点/LLM token/OCR 页数/律师改动/登录账号名；只记 key 不记取值，`tests/test_log_secrecy.py` 守着；按天切分、保留 `RUN_LOG_RETENTION_DAYS`；律师改动对比 checkpoint 里交给律师时的字段，重启不丢；`scripts/usage_report.py` 按账号统计。#75：节点内的 llm_call / ocr_batch 自动带 `node`，llm_call 带耗时与提示词版本（模板文件名@哈希），ocr_batch 带单页耗时分布与超时 / 限流页数，节点出错记固定 `error_category`（`services/error_category.py`），另有 `dashscope_key_switch`（OCR 与 LLM 备用路径改用付费 key，`path` 区分）、resume 的 `wait_s`、过期仍停在断点的 `abandoned`；`scripts/cost_report.py` 按 `.env` 的 `COST_PRICES` 汇总每案 / 每节点 / 每模型成本、错误类别、断点等待中位数与放弃率）
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

- **依赖导入与异常处理**：必需依赖应在依赖文件中明确声明；缺失或版本不兼容时明确报错并修复依赖配置。不得仅为使代码运行而堆叠 `try/except ImportError`、尝试多个替代库，或在导入失败时静默跳过功能。只有明确设计为可选依赖或兼容层时，才允许导入降级，并须说明触发条件、行为差异及验证方式。按文件格式选择不同解析库不属于导入降级。
- LLM API：读取超时 120s、单次调用总时限 180s（DeepSeek 排队时回空行保活，读取超时不会触发），失败重试 1 次（关闭 SDK 自带重试，否则一次超时要等 6 分钟），返回中文错误信息；DeepSeek 超时（含总时限）/ 连不上 / 5xx / 429 / 402 时不再重试 DeepSeek，直接改用备用模型 `LLM_FALLBACK_MODEL`（默认 qwen3.8-flash，DashScope key 与 OCR 共用），之后 10 分钟内都走备用模型；key 无效（401）属配置错误，不切换
- OCR：timeout 120s，失败返回 `{ error: "OCR 处理失败：具体原因" }`；同步的 DashScope 请求只在 `ocr_engine` 的专用线程池里跑，全局名额 `OCR_GLOBAL_CONCURRENCY` 排队（排队时间不算单页超时）；上传时 PDF / Word 解析也在专用线程里跑。不要把阻塞调用放进默认线程池（`asyncio.to_thread` / `run_in_executor(None)`），2 核服务器默认池只有 6 个线程，占满后登录校验等所有请求都会卡住
- 文件解析：不支持格式返回明确错误，不静默失败
- JSON 解析：优先用结构化输出；仍需兜底去除 markdown 代码块标记后重试
- Agent：成本超限（OCR 总页数用完）→ 停止 OCR，并在相关字段出处与起诉状里注明「扫描识别页数已达上限」（违约金 / 利息不按「合同未约定」套 LPR，验收报告没读到时日期 / 台数注明可能不全），不静默烧钱、不静默降级
- 前端所有用户提示一律中文
- 后端日志统一用 `logging`（`main.py` 配置，输出到 stdout），不用 `print`；stdout 与运行日志都不写案件字段取值或 OCR 原文

### Documentation Best Practices

- **Location**：CLAUDE.md 在根目录；Prompt 在 `prompts/`；前后端文档各自目录内
- **Change Logs**：优先更新已有文件，不另建新文件

---

## 应用流程

### 用户视角（单页面 Stepper，共 4 步）

```
Step 1: 上传文件
  - 拖拽/点击上传，支持 .pdf / .docx；单个文件 ≤ 20MB，一次合计 ≤ 95MB（服务器 nginx 限 100MB，`lib/upload-limits.ts`），超了前端直接中文提示，不发请求
  - 联网查询开关（默认开启）
  - "上传并识别" → POST /api/upload
  - 材料确认：逐个显示系统识别的文件类型（审批表 / 合同 / 补充协议 / 验收报告 / 战略或框架合同 / 其他），
    认错可下拉改，认不出的标琥珀色；按改后的类型重算缺少哪些材料（`lib/materials.ts`）
  - "确认材料，开始分析" → 进入 Step 2，/api/analyze 按律师确认的类型处理；改过的类型只记
    「从什么改成什么」到运行日志（`file_type_override`，不记文件名）

Step 2: AI 处理中（agent 运行，SSE 进展）
  - 步骤条：清点材料 → 抽取字段 → 交叉校验
  - 每步摘要（"识别到3份文件""抽取到23个字段""发现2个冲突"）
  - agent 命中断点 → 直接进入 Step 3 的对应待决项
  - 出错 → 中文错误 + 重试 → POST /api/analyze

Step 3: 审核确认 ⭐ 核心页面（agent 断点的 UI）
  - 左侧：字段表格（字段名中文 | 值 | 来源出处 | 状态）
    - 状态：✅ 正常 / ❌ 缺失 / ⚠️ 冲突 / 🔍 待核实(OCR)
    - 按 当事人 / 合同与金额 / 付款与违约条款 / 管辖与文书 分组（`lib/field-groups.ts`），组标题写需处理项数；
      「只看需处理的」勾选后只显示缺失 / 冲突 / 待核实的字段
    - 按 confidence 排序，低分默认展开；数字放 hover
    - 冲突字段展开各来源值，律师选择
    - 所有字段可点击编辑
  - 右侧：校验报告 + 高亮列表
  - 律师决定 → POST /api/resume（可多次往返直至无待决）
  - 底部："确认并生成起诉状"
  - 处理 / 审核 / 预览阶段刷新或关闭页面先弹浏览器确认（`hooks/useLeaveWarning.ts`）；会话编号与字段修改存本标签页
    sessionStorage（`lib/case-session.ts`，关标签页即清除），刷新后上传页问「继续上次的案件？」→ GET /api/case 回到审核页；
    处理中刷新则存的是分析请求编号，「继续」→ GET /api/analyze/result 接回同一次分析（还在跑就等它跑完，不重新上传、不重复计费）

Step 4: 预览下载
  - 起诉状全文预览（标注保留）
  - "下载 Word"（docx-js 模板填槽），文件名「原告诉被告起诉状_YYYYMMDD.doc」（仲裁为「…仲裁申请书_…」，当事人名称还是【待补充】时退回「起诉状_日期.doc」，`lib/complaint-filename.ts`）
  - "返回修改" → POST /api/generate
  - "重新开始" 先弹确认（会清空审核修改与生成的文书）
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
    ├── 扫描审批表整份 OCR（approval_ocr，上限 AGENT_MAX_APPROVAL_OCR_PAGES）
    ├── 清点节点（代码按文件类型判定）→ 缺材料只提醒、照常继续
    ├── 抽取节点（Prompt A-2，结构化输出）
    │     ├── 主取审批表结构字段
    │     ├── 缺条款 → search_in_docs / ocr_page 定向补（扫描合同按需）
    │     └── 自校验：值回原文命中？否 → 标待核实
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
| `prompt-b-generate.md` | v1 遗留；模板渲染后仅作兜底 | — | 文本 |
| `complaint-template.md` | `services/`（模板常量） | 模板 | — |

> 材料清点不再调 LLM（`services/materials.py`）。交叉校验的"判断"已移出 prompt，改由 `services/validators.py` 完成；`prompt-a-validate.md` 只产出面向律师的自然语言说明。

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

### Claude Code 辅助（`.claude/`）

- `hooks/session-start.sh`：云端会话启动时安装后端 requirements 与前端依赖（与 CI 一致），ruff / pytest / ESLint / build 开箱可跑。后端一律用 `python -m pip` / `python -m ruff` / `python -m pytest`：云端镜像的 `pip` 可能属于另一个 Python，镜像预装的 ruff / pytest 版本也不同、在 PATH 里排在前面；云端容器闲置会被回收，scratchpad（含真实案卷）随之清空
- `skills/run-app`：启动前后端（数据隔离到临时目录），Playwright 走完四步截图，并保存分析结果与生成的诉状文本
- `skills/case-review`：律师发来新案卷时解压、盘点材料、看扫描页，用系统跑一遍并与律师诉状逐句对比
- `skills/lawyer-questionnaire`：把要问律师的问题生成为 Word 确认单（律师在中国大陆，确认单一律用 Word），并说明如何读取回复
- 真实案卷只放会话临时目录（scratchpad），**绝不提交到仓库**，测试一律用虚构数据

### 案件 eval（`backend/scripts/eval_cases.py`）

- 用律师已写好诉状的真实案卷跑系统（与网页同一条链路，不经浏览器），按 `expected.json` 的标准字段逐项打分：正确 / 错误 / 缺失 / 待定（待律师确认，不计分）；按字段比，不逐句比诉状（律师措辞本就不统一）
- 标准字段可选填 `source`（预期来源文件）与 `review: "待核实"`：来源按值回原文重新锚定后实际命中的文件判断（不信模型自述）；审核页显示「正常」却值错 / 应标待核实 / 回原文未命中的记为静默错误。两项单独计数、不影响准确率，前后两次运行对比也列出（`case_eval/scoring.py`）
- OCR / LLM 返回按请求内容哈希录制到案件的 `cache/`：改规则后 `--strict` 只回放、零费用；换模型或改提示词后 `--mode live` 重建
- 每次运行写 `_runs/<时间>/report.md`，并与上次逐字段对比；落实律师答复、改抽取规则后跑一遍，看有没有改坏别的案件
- 真实案卷与标准字段放服务器 `/srv/eval-cases`（见 `deploy/README.md`「案件 eval」），不进仓库；新案卷核对完（`case-review`）就补一份 `expected.json` 加进去

---

## 迁移路线（v1 流水线 → v2 agentic）

按增量推进，每步可独立验证，不一次性推翻：

1. **工具层先行**：把校验/评分/锚定实现为 `services/` 纯函数（`validators.py`、`confidence.py`、`anchoring.py`），配单测。此步不动流程，即可提升可靠性。
2. **抽取结构化**：`llm_client.py` 启用 function calling/结构化输出，`FieldValue` 加 `confidence/page/anchor`，抽取后做原文锚定，未命中标待核实。
3. **校验移出 LLM**：`/api/extract` 拆分——LLM 抽取 + 代码校验；`prompt-a-validate.md` 降级为说明文本。
4. **模板渲染**：`/api/generate` 与前端 `doc-generator.ts` 改为模板填槽，标注由代码贴。
5. **引入 agent 编排**：`agent/graph.py` 用 LangGraph 串起节点，加入 `interrupt` 断点；`/api/extract` → `/api/analyze` + `/api/resume`；前端审核页支持多次往返。
6. **按需 OCR 与检索**：扫描合同不再整份 OCR，改 `ocr_page` + `search_in_docs` 定向补齐。
7. **联网工具**：`company_lookup.py` 接企业信息查询，替代 LLM 猜名称。

---

## 不包含（后续迭代预留）

- 数据库（PostgreSQL）— 案件持久化（agent checkpoint 届时落 DB）
- 账号权限分级（目前所有登录账号权限相同，只是各自只能访问自己发起的案件；原告信息表更新另凭管理员口令）
- 多模态 LLM 直传扫描件（替代 OCR，进一步砍误差链）
- 批量案件处理
- Node.js 后端（Fastify）
- BullMQ 异步任务队列
- S3 / 云存储
- 起诉状版本对比
- 操作审计日志
- **LangChain 本体**（仅采用 LangGraph 的图/检查点，不引入其余生态）
- 向量检索（v2 条款定位先用关键词；量大再上向量）
```

## Agent skills

### Issue tracker

需求规格与任务使用 `phootako630/legal-doc-gen` 的 GitHub Issues；读写 tracker 前查看 `docs/agents/issue-tracker.md`。

### Triage labels

使用 Matt 默认五种 triage 标签；分配角色标签前查看 `docs/agents/triage-labels.md`。

### Domain docs

采用 single-context：`GLOSSARY.md` + `docs/adr/`；探索代码前遵循 `docs/agents/domain.md`。
