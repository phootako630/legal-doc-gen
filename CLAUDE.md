# CLAUDE.md — 安装合同纠纷起诉状自动化系统（v2 · Agentic）

## 中文法律领域与 skills 约定

- **默认语言**：需求澄清、skill 执行中的提问、方案讨论、代码评审、领域文档、issue 和 PR 默认使用简体中文。面向律师的界面、错误提示、审核说明和生成文书必须使用中文。
- **技术标识保留原文**：代码标识符、API 字段、命令、文件路径和已有 issue 标签不翻译、不因中文交流而改名。英文 skill 指令可直接使用，执行时按本项目语境解释。
- **中文术语为准**：领域建模以材料和项目中使用的中文法律术语为准；英文译名仅作辅助，不作为字段含义的依据。不得将“法定代表人”与“负责人”、“违约金”与“利息”、“检验合格日期”与“验收日期”等概念直接视为同义词。字段映射或业务归类必须保留原始称谓，说明依据；无法确定时交由律师确认。
- **证据原文保留**：主体名称、合同编号、付款条款、违约条款、争议解决条款和来源片段保留原文，不翻译成英文后再回译，不润色或静默纠正 OCR。需要格式归一化时保留原值与来源，并将归一化值与引用原文区分开。
- **法律语境明确**：通用 skills 不得默认套用其他法域的诉讼流程或法律概念。涉及法律适用、管辖、请求权或计算规则的变化，需明确依据与适用时间；不得凭 skill 中的示例推导法律结论，最终判断仍由律师确认。
- **验证覆盖中文材料**：修改解析、OCR、抽取、来源锚定、金额日期处理或文书渲染时，使用与改动相关的中文样例验证；重点关注中文标点、全角字符、换行、扫描识别错误及金额单位。测试材料一律用合成或脱敏数据。
- **开发指令与运行时分开**：本文件约束编码助手；应用中的模型行为由实际代码和 `prompts/` 决定。修改本文件或 `docs/agents/` 不能替代运行时 prompt 修改或功能验证。这些文档与代码不一致时，以代码为准。

---

## 项目概述

面向律师团队的 Web 应用，用于自动化处理安装合同纠纷案件的民事起诉状准备工作。律师上传案件文件，agent 自动完成材料清点、信息抽取、交叉校验，律师确认后生成起诉状 Word 文档。

**核心用户**：中国大陆律师团队，不懂技术，不看 JSON，不看英文。

**目标**：验证 AI 抽取准确率和律师使用体验。支持可解析 PDF、扫描件 PDF（OCR）和 Word 文件。登录：账号由管理员在服务器上用 `scripts/manage_users.py` 创建（无注册入口），会话 Cookie 7 天免登录，律师可自行修改密码；部署时强制开启（`AUTH_ENABLED=1`），本地开发与单测默认关闭；案件状态（agent checkpoint、上传的扫描件）落盘到 `backend/data/`（SQLite + 文件），服务重启后仍可 resume，超过 `CASE_RETENTION_DAYS`（默认 7 天）自动清除（服务启动即清、之后每小时清，resume / 读取时也拒绝过期数据）；落盘文件仅属主可读写（目录 0700、文件 0600）；不引入业务数据库。

---

## 核心原则

- **AI 不做最终判断**：agent 只负责抽取、校验、提议，律师负责确认和修改；关键分歧点 agent 必须暂停问律师（human-in-the-loop），不得自行拍板
- **出处可追溯**：每个字段值必须标注来源文件 + 位置（页码/锚点），且值必须能在原文精确命中
- **宁缺勿造**：无法确定的字段标为缺失（value=null），绝不编造；抽取值回原文比对失败一律标「待核实」（来源注明「原文未找到」，审核页与起诉状都提醒律师），不当作正常值写入文书
- **校验用代码，不用 LLM**（①②）：可量化的一致性检查——金额勾稽、台数三源比对、格式校验——一律由确定性 Python 代码完成，LLM 不做算术判断。LLM 只负责"抽出数字"，"比对"由代码算
- **可信度分层**（③）：律师不信任黑盒总分。硬校验（是/否结论）定状态，软估计（0–100 confidence）只用于 UI 排序和默认展开，不替代律师判断
- **模板渲染，不自由生成**（④）：起诉状是固定法律格式，LLM/agent 只产出结构化字段，由模板确定性渲染，LLM 不写全文

---

## 安全红线

- **真实案卷**：律师发来的真实案卷只放会话临时目录（scratchpad），**绝不提交到仓库**（eval 用的真实案卷集放服务器，见 [case-eval.md](docs/agents/case-eval.md)）
- **API Key**：DeepSeek、DashScope、企业查询等 API Key 放后端，不暴露前端
- **日志不含案件内容**：stdout 与运行日志都不写案件字段取值或 OCR 原文

---

## 技术栈

### 前端

```
框架：       React 19 + Vite + TypeScript（strict mode）
样式：       Tailwind CSS
UI 组件：    shadcn/ui
表单：       react-hook-form + zod
文档生成：   `lib/html-doc-generator.ts`（浏览器端把起诉状全文生成 Word 兼容 HTML 的 .doc）
路由：       无（单页面 Stepper 组件）
实时进度：   SSE / fetch stream（接收 agent 的分步进展与暂停点）
```

### 后端（Python）

```
框架：         FastAPI
Agent 编排：   LangGraph（状态图 + interrupt 断点 + checkpoint；仅用图与检查点，不引入整个 LangChain 生态）
文件解析：     PyMuPDF（fitz）提取 PDF 文本；python-docx 提取 Word 文本
OCR：          Qwen 多模态云端 API（阿里云 DashScope，默认 qwen3.8-flash；免费额度 key + 付费备用 key）；备用 RapidOCR（本地 ONNX，代码注释保留）
LLM 调用：     OpenAI SDK（兼容 DeepSeek API），JSON 模式（`response_format=json_object`）；DeepSeek 不可用时改用通义 qwen3.8-flash（DashScope 兼容接口）
模板渲染：     `services/complaint_renderer.py` 按 `prompts/complaint-template.md` 填槽
企业信息查询： 联网工具（企查查/天眼查/公示系统 API，按信用代码补全法人全称）
运行方式：     uvicorn，单进程
```

---

## 按需阅读（`docs/agents/`）

- 改派生字段、起诉状模板 / 渲染、原告信息表，或落实律师答复 → [complaint-rules.md](docs/agents/complaint-rules.md)：起诉状生成、律师确认的填写规则、改动清单、原告信息表
- 改上传解析、agent 节点 / 图、按需 OCR 或抽取流程 → [agent-pipeline.md](docs/agents/agent-pipeline.md)：材料可靠性分级、工具清单、断点、数据流、框架取舍
- 改交叉校验、可信度、来源锚定或就绪度 → [validation.md](docs/agents/validation.md)：校验项、硬 / 软信号、状态映射
- 改后端路由或前后端接口 → [api.md](docs/agents/api.md)：全部路由、登录与会话隔离、原告信息表接口
- 改前端页面或审核页交互 → [app-flow.md](docs/agents/app-flow.md)：律师视角的四步流程
- 跑或改案件 eval、补虚构案卷 → [case-eval.md](docs/agents/case-eval.md)：打分、录制回放、baseline、归因
- 本地启动、看 UI、改 `.claude/` 的 hook / skill → [local-dev.md](docs/agents/local-dev.md)：启动命令与 Claude Code 辅助
- 查目录结构、共享类型、字段中英名、配置项、prompt 加载位置 → [code-reference.md](docs/agents/code-reference.md)：代码镜像，以代码为准

---

## 代码规范

### Codebase Management

- **Code Standards**：
  - 前端：TypeScript strict mode，不允许 `any`
  - 后端：Python 3.10+，type hints，Pydantic 做请求/响应与 CaseState 模型
- **Documentation**：每个文件顶部一行注释说明职责；复杂业务逻辑必须注释意图
- **Formatting**：
  - 前端：Prettier（`printWidth: 100`, `singleQuote: true`, `semi: true`）+ ESLint
  - 后端：Ruff（lint + format）
  - 导入顺序：标准库 → 第三方 → 项目内
- **后端命令**：一律用 `python -m pip` / `python -m ruff` / `python -m pytest`：云端镜像的 `pip` 可能属于另一个 Python，镜像预装的 ruff / pytest 版本也不同、在 PATH 里排在前面

### Feature Implementation

- **分层**：业务逻辑放 `lib/`（前端）或 `services/`（后端），不写在组件/路由里；**工具逻辑放 `services/`，编排逻辑放 `agent/`，二者不混**
- **Naming**：
  - 前端：组件 PascalCase（`FieldTable.tsx`），工具 kebab-case（`field-map.ts`）
  - 每个文件单一职责，组件不超过 200 行

### Error Handling

- **依赖导入与异常处理**：必需依赖应在依赖文件中明确声明；缺失或版本不兼容时明确报错并修复依赖配置。不得仅为使代码运行而堆叠 `try/except ImportError`、尝试多个替代库，或在导入失败时静默跳过功能。只有明确设计为可选依赖或兼容层时，才允许导入降级，并须说明触发条件、行为差异及验证方式。按文件格式选择不同解析库不属于导入降级。
- LLM API：读取超时 120s、单次调用总时限 180s（DeepSeek 排队时回空行保活，读取超时不会触发），失败重试 1 次（关闭 SDK 自带重试，否则一次超时要等 6 分钟），返回中文错误信息；DeepSeek 超时（含总时限）/ 连不上 / 5xx / 429 / 402 时不再重试 DeepSeek，直接改用备用模型 `LLM_FALLBACK_MODEL`（默认 qwen3.8-flash，DashScope key 与 OCR 共用），之后 10 分钟内都走备用模型；key 无效（401）属配置错误，不切换
- OCR：timeout 120s，失败返回 `{ error: "OCR 处理失败：具体原因" }`；同步的 DashScope 请求只在 `ocr_engine` 的专用线程池里跑，全局名额 `OCR_GLOBAL_CONCURRENCY` 排队（排队时间不算单页超时）；上传时 PDF / Word 解析也在专用线程里跑。不要把阻塞调用放进默认线程池（`asyncio.to_thread` / `run_in_executor(None)`），2 核服务器默认池只有 6 个线程，占满后登录校验等所有请求都会卡住
- 文件解析：不支持格式返回明确错误，不静默失败
- JSON 解析：优先用结构化输出；仍需兜底去除 markdown 代码块标记后重试
- Agent：成本超限（OCR 总页数用完）→ 停止 OCR，并在相关字段出处与起诉状里注明「扫描识别页数已达上限」（违约金 / 利息不按「合同未约定」套 LPR，验收报告没读到时日期 / 台数注明可能不全），不静默烧钱、不静默降级
- 后端日志统一用 `logging`（`main.py` 配置，输出到 stdout），不用 `print`；内容限制见「安全红线」

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
- 向量检索（条款定位目前用关键词；量大再上向量）

## Agent skills

### Issue tracker

需求规格与任务使用 `phootako630/legal-doc-gen` 的 GitHub Issues；读写 tracker 前查看 `docs/agents/issue-tracker.md`。

### Triage labels

使用 Matt 默认五种 triage 标签；分配角色标签前查看 `docs/agents/triage-labels.md`。

### Domain docs

采用 single-context：`GLOSSARY.md` + `docs/adr/`；探索代码前遵循 `docs/agents/domain.md`。
