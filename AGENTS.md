# 安装合同纠纷起诉状自动化系统（v2 · Agentic）

写、改或评审代码 → 先读 [CODING_STANDARDS.md](CODING_STANDARDS.md)。

## 中文法律领域约定

- **默认语言**：需求澄清、skill 执行中的提问、方案讨论、代码评审、领域文档、issue 和 PR 默认使用简体中文。面向律师的界面、错误提示、审核说明和生成文书必须使用中文。
- **技术标识保留原文**：代码标识符、API 字段、命令、文件路径和已有 issue 标签不翻译、不因中文交流而改名。英文 skill 指令可直接使用，执行时按本项目语境解释。
- **中文术语为准**：领域建模以材料和项目中使用的中文法律术语为准；英文译名仅作辅助，不作为字段含义的依据。不得将“法定代表人”与“负责人”、“违约金”与“利息”、“检验合格日期”与“验收日期”等概念直接视为同义词。字段映射或业务归类必须保留原始称谓，说明依据；无法确定时交由律师确认。
- **证据原文保留**：主体名称、合同编号、付款条款、违约条款、争议解决条款和来源片段保留原文，不翻译成英文后再回译，不润色或静默纠正 OCR。需要格式归一化时保留原值与来源，并将归一化值与引用原文区分开。
- **法律语境明确**：通用 skills 不得默认套用其他法域的诉讼流程或法律概念。涉及法律适用、管辖、请求权或计算规则的变化，需明确依据与适用时间；不得凭 skill 中的示例推导法律结论，最终判断仍由律师确认。
- **开发指令与运行时分开**：本文件约束编码助手；应用中的模型行为由实际代码和 `prompts/` 决定。修改本文件、`CODING_STANDARDS.md` 或 `docs/agents/` 不能替代运行时 prompt 修改或功能验证。这些文档与代码不一致时，以代码为准。

## 项目概述

面向律师团队的 Web 应用，用于自动化处理安装合同纠纷案件的民事起诉状准备工作。律师上传案件文件（可解析 PDF、扫描件 PDF、Word），agent 完成材料清点、信息抽取、交叉校验，律师确认后生成起诉状 Word 文档。目标是验证 AI 抽取准确率和律师使用体验。

**核心用户**：中国大陆律师团队，不懂技术，不看 JSON，不看英文。

## 核心原则

- **AI 不做最终判断**：agent 只负责抽取、校验、提议，律师负责确认和修改；关键分歧点 agent 必须暂停问律师（human-in-the-loop），不得自行拍板
- **出处可追溯**：每个字段值必须标注来源文件 + 位置（页码/锚点），且值必须能在原文精确命中
- **宁缺勿造**：无法确定的字段标为缺失（value=null），绝不编造；抽取值回原文比对失败一律标「待核实」（来源注明「原文未找到」，审核页与起诉状都提醒律师），不当作正常值写入文书
- **校验用代码，不用 LLM**（①②）：可量化的一致性检查——金额勾稽、台数三源比对、格式校验——一律由确定性 Python 代码完成，LLM 不做算术判断。LLM 只负责"抽出数字"，"比对"由代码算
- **可信度分层**（③）：律师不信任黑盒总分。硬校验（是/否结论）定状态，软估计（0–100 confidence）只用于 UI 排序和默认展开，不替代律师判断
- **模板渲染，不自由生成**（④）：起诉状是固定法律格式，LLM/agent 只产出结构化字段，由模板确定性渲染，LLM 不写全文

## 安全红线

- **真实案卷**：律师发来的真实案卷只放会话临时目录（scratchpad），**绝不提交到仓库**（eval 用的真实案卷集放服务器，见 [case-eval.md](docs/agents/case-eval.md)）

## 技术选择

依赖与版本以 `frontend/package.json`、`backend/requirements.txt` 为准；以下是从清单看不出的选择：

- 前端单页 Stepper，无路由；agent 进展经 SSE / fetch stream 推送
- Word 导出：前端 `lib/html-doc-generator.ts` 把起诉状全文生成 Word 兼容 HTML 的 .doc（不用 docx 库）；正文由后端 `services/complaint_renderer.py` 按 `prompts/complaint-template.md` 填槽
- LLM：OpenAI SDK 调 DeepSeek，DeepSeek 不可用时改用 DashScope 的 qwen；OCR：DashScope Qwen 多模态（RapidOCR 备用方案只在 `ocr_engine.py` 注释里）
- 后端 uvicorn 单进程：登录锁定、限流等状态在进程内存；案件状态（checkpoint、上传文件）落盘 `backend/data/`（SQLite + 文件），不引入业务数据库

## 按需阅读（`docs/agents/`）

- 改派生字段、起诉状模板 / 渲染、原告信息表，或落实律师答复 → [complaint-rules.md](docs/agents/complaint-rules.md)：起诉状生成、律师确认的填写规则、改动清单、原告信息表
- 改上传解析、agent 节点 / 图、按需 OCR 或抽取流程 → [agent-pipeline.md](docs/agents/agent-pipeline.md)：材料可靠性分级、工具清单、断点、OCR 预算、数据流
- 改交叉校验、可信度、来源锚定或就绪度 → [validation.md](docs/agents/validation.md)：校验项、硬 / 软信号、状态映射
- 改后端路由、前后端接口、登录或案件数据保留 → [api.md](docs/agents/api.md)：全部路由、登录与会话隔离、案件过期清除、原告信息表接口
- 改前端页面或审核页交互 → [app-flow.md](docs/agents/app-flow.md)：律师视角的四步流程
- 跑或改案件 eval、补虚构案卷 → [case-eval.md](docs/agents/case-eval.md)：打分、录制回放、baseline、归因
- 本地启动、看 UI、改 `.claude/` 的 hook / skill → [local-dev.md](docs/agents/local-dev.md)：启动命令与 Claude Code 辅助

## 不在范围内（后续迭代）

业务数据库（PostgreSQL）、账号权限分级（目前所有登录账号权限相同，各自只能访问自己发起的案件；原告信息表更新另凭管理员口令）、多模态 LLM 直传扫描件、批量案件处理、Node.js 后端（Fastify）、BullMQ 任务队列、S3 / 云存储、起诉状版本对比、操作审计日志、向量检索（条款定位用关键词）。

## Agent skills

- **Issue tracker**：需求规格与任务使用 `phootako630/legal-doc-gen` 的 GitHub Issues；读写 tracker 前查看 `docs/agents/issue-tracker.md`。
- **Triage labels**：使用 Matt 默认五种 triage 标签；分配角色标签前查看 `docs/agents/triage-labels.md`。
- **Domain docs**：采用 single-context：`GLOSSARY.md` + `docs/adr/`；探索代码前遵循 `docs/agents/domain.md`。
