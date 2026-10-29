# 编码规范

写、改或评审代码时读本文件。律师确认的填写规则等领域事实不在这里，见 [AGENTS.md](AGENTS.md)「按需阅读」。

## 分层与文件

- 业务逻辑放 `frontend/src/lib/`（前端）与 `backend/app/services/`（后端）；组件只管展示与交互，路由只管收发请求。
- **工具逻辑放 `services/`，编排逻辑放 `agent/`**：解析、OCR、校验、查询、渲染写成不依赖 LangGraph 的纯 Python 函数 / service，可单独单测，换编排器不用改。
- 每个文件单一职责，组件不超过 200 行。前端组件 PascalCase（`FieldTable.tsx`），工具模块 kebab-case（`field-map.ts`）。
- 每个文件顶部一行注释说明职责；复杂业务逻辑注释意图。
- `frontend/src/components/ui/` 是 shadcn/ui 生成代码：用 shadcn CLI 增改，源码保持生成时原样（ESLint 也跳过此目录）。

## 类型与格式

- 前端：TypeScript strict，类型写具体，`any` 由 ESLint 报错；风格照现有代码（单引号、分号、行宽 100），导入顺序 标准库 → 第三方 → 项目内。
- 后端：Python 3.10+，带 type hints；请求 / 响应与 CaseState 用 Pydantic。
- 后端 lint 门槛是 `python -m ruff check .`（CI 只跑 check）。仓库没有整体跑过 `ruff format`，格式化只作用于自己新写的代码，保持 diff 只含真实改动。
- CI 跑的全部检查见 `.github/workflows/ci.yml`（后端 ruff + pytest、虚构案卷集 eval、前端 lint + vitest + 构建）。

## 依赖与框架

- 必需依赖写进 `backend/requirements.txt` / `frontend/package.json`；缺失或版本不兼容时明确报错并修好依赖配置。导入失败就让它失败：不堆叠 `try/except ImportError`、不轮流尝试替代库、不静默跳过功能。只有明确设计为可选依赖或兼容层时才允许导入降级，并注明触发条件、行为差异与验证方式。按文件格式选不同解析库不属于导入降级。
- agent 只用 LangGraph 的图 + checkpoint + interrupt，不引入 LangChain 本体：抽象层深、prompt 不透明、版本易 breaking，对"可审计"要求高的法律场景是负担。结构化输出、重试等零件自行实现或用轻量库。
- checkpoint 存 SQLite（`agent/checkpoint.py`）；将来接 PostgreSQL 时只替换 saver。

## agent 节点

- interrupt 断点节点里只放便宜、无副作用的代码：resume 时 LangGraph 会从头重跑被打断的节点，所以 OCR / LLM 放在断点前的独立节点（`contract_scan` 与 `qty_check` 分开即为此）。

## 阻塞调用与线程池

- 同步阻塞的活（DashScope 等同步 HTTP、PDF 解析 / 渲染）放模块自己的专用 `ThreadPoolExecutor`，参照 `ocr_engine._OCR_POOL`、`file_parser._PARSE_POOL`、`page_image._VIEW_POOL`。默认线程池（`asyncio.to_thread` / `run_in_executor(None)`）留给登录校验等短请求：2 核服务器默认池只有 6 个线程，占满后所有请求都会卡住。
- OCR 一律经 `services/ocr_engine`：专用线程池 + 全局名额 `OCR_GLOBAL_CONCURRENCY` 排队，排队时间不算单页超时。

## LLM、OCR 与解析出错

- LLM 一律经 `services/llm_client.chat`；JSON 输出用 `json_mode=True`（`response_format=json_object`，并兜底剥掉 markdown 代码块与 `{"type":"json_object"}` 外壳后解析）。超时、重试、备用模型的取值与理由在 `config.py` 与 `llm_client.py` 注释里；改这块时守住：SDK 自带重试关闭（`max_retries=0`），失败只重试 1 次；DeepSeek 超时（含总时限）/ 连不上 / 5xx / 429 / 402 直接改用 `LLM_FALLBACK_MODEL` 并冷却 `LLM_FALLBACK_COOLDOWN_S`；key 无效（401）属配置错误，不切换；报错信息用中文。
- OCR 超时：单页 `OCR_PAGE_TIMEOUT`、整份 `OCR_TIMEOUT`（`config.py`）；失败抛中文 `RuntimeError`（「OCR 处理失败：具体原因」「第 N 页 OCR 超时」），由调用方写进字段出处提醒律师。
- 文件解析：不支持的格式抛 `UnsupportedFileError` 并给出中文说明。
- OCR 页数预算用完（`AGENT_MAX_TOTAL_OCR_PAGES` 及各子上限）→ 停止 OCR，在相关字段出处与起诉状里注明「扫描识别页数已达上限」（`derived_fields.OCR_BUDGET_HINT`）：违约金 / 利息不按「合同未约定」套 LPR，验收报告没读到时日期 / 台数注明可能不全。成本超限必须让律师看见，不静默降级。

## 安全与保密

- API Key（DeepSeek、DashScope、企业查询）只在后端经 `config.py` 读取，前端代码与构建产物里不出现。
- 后端日志用 `logging`（`main.py` 配置，输出到 stdout），不用 `print`。stdout 与运行日志只记字段 key、数量、页码、耗时、token，不写案件字段取值、文件名（常含当事人名称）或 OCR 原文；`tests/test_log_secrecy.py` 跑完整流程守着这条。
- 落盘到 `backend/data/` 的文件仅属主可读写：目录 0700、文件 0600（参照 `services/file_store.py`）。

## 测试与验证

- 后端命令一律用 `python -m pip` / `python -m ruff` / `python -m pytest`：云端镜像的 `pip` 可能属于另一个 Python，镜像预装的 ruff / pytest 版本也不同、在 PATH 里排在前面。
- 测试材料一律用虚构、合成或脱敏数据。
- 改解析、OCR、抽取、来源锚定、金额日期处理或文书渲染时，用与改动相关的中文样例验证；重点覆盖中文标点、全角字符、换行、扫描识别错误及金额单位。
- 新规则或改抽取：补单测（如 `backend/tests/test_derived_fields.py`），再往虚构案卷集补一个能体现该规则的案件并跑案件 eval 回归，步骤见 [docs/agents/case-eval.md](docs/agents/case-eval.md)。

## Prompt

- 提示词在 `prompts/`，经 `services/prompt_loader.load_prompt` 读取（grep 文件名即见加载处）。`prompt-b-generate.md` 是 v1 遗留，代码不再加载，改它不影响运行。
