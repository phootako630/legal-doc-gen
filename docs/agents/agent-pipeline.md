# 材料、agent 编排与按需 OCR

改上传解析、文件类型识别、agent 节点 / 图、按需 OCR 或抽取流程时读本文件。OCR 页数预算等护栏取值见 [code-reference.md](code-reference.md)「后端配置」；出错与成本超限的处理见 [CLAUDE.md](../../CLAUDE.md)「Error Handling」。

## 真实材料形态与来源可靠性分级

本系统处理的一个典型案件通常含三类材料，**形态与可靠性差异很大，agent 必须区别对待**：

| 材料类别 | 典型文档 | 形态 | 可靠性 | agent 处理策略 |
|---|---|---|---|---|
| **审批表** | KA 大客户收款不良合同诉讼审批表 | 可解析文本、**键值表单** | ⭐⭐⭐ 最高（结构化，逐项直取） | **主数据源**：合同类型、合同分公司、被告名称、联系人、合同号、台数（签约/实际）、合同总额、已付/未付、最后付款日等字段直接取此（被告住址不取此，见 [complaint-rules.md](complaint-rules.md)「被告住址 / 法定代表人」） |
| **验收/检验报告** | 电梯监督检验报告（监检报告） | 可解析文本 + 盖章图；每台电梯一份 | ⭐⭐ 中高 | **交叉校验源**：被告统一社会信用代码、安装地点、检验合格日期（→最晚验收日）、台数（验收口径 = 报告份数/设备代码数） |
| **安装合同** | 电梯安装合同 | 常为**纯扫描件**（需 OCR，页数多） | ⭐ OCR 有噪声，需降权+待核实 | **条款文本源**：付款条款、违约利率条款、争议解决条款；台数（合同口径）。OCR 成本高，**按需只读相关页，不整份全抽** |

**由此确立的抽取策略（agentic 的核心价值）**：

1. 优先从**审批表**批量拿结构字段（便宜、可靠）；
2. 用**验收报告**补信用代码/安装地点/验收日期，并做台数、名称交叉校验；
3. 仅当仍缺条款类字段时，才对**扫描合同**做 OCR + 条款关键词定位，**只 OCR 需要的页**，避免整份扫描件全量 OCR 的成本与噪声；

> 注：字段 `identified_type` 的"验收报告"在实务中常表现为"电梯监督检验报告"，识别时二者归为同一类。

---

## Agentic 架构

### 组成

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator（LangGraph 状态图）                          │
│  节点：清点 → 抽取(带工具) → 校验(代码) → [interrupt:律师]       │
│  （生成不在图内，走 POST /api/generate）                        │
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

### 工具清单（`backend/app/services/`）

| 工具 | 职责 | 备注 |
|---|---|---|
| `parse_file(upload_file)` | PDF/Word → 文本 + 是否扫描件 + 页数 | PyMuPDF / python-docx |
| `ocr_page(pdf_bytes, page_num)` | 对**指定页**做 OCR | 按需调用，非整份；Qwen-VL-OCR |
| `search_in_docs(pages, keywords)` | 回原文定位条款/字段所在页与片段 | 长合同用，省 token |
| `check_amounts(total, paid, unpaid)` | 总额 == 已付 + 未付；返回是否勾稽 | 确定性代码 |
| `check_elevator_qty(by_approval, by_contract, by_acceptance)` | 签约/合同/验收三源台数比对 | 确定性代码 |
| `validate_credit_code(code)` | 统一社会信用代码 18 位含校验位 | 确定性代码 |
| `lookup_company(credit_code)` | 按信用代码补全法人全称 | 联网工具，替代 LLM 猜 |
| `render_complaint(fields)` | 结构化字段 → 起诉状（模板填槽） | 见 [complaint-rules.md](complaint-rules.md) |

### Agent 循环与 human-in-the-loop

- agent 在抽取节点可多轮：抽取 → 自校验（值回原文命中？）→ 命中失败标待核实 → 缺关键字段则 `search_in_docs` / `ocr_page` 定向补齐。
- 护栏：成本上限（全案 OCR 总页数 `AGENT_MAX_TOTAL_OCR_PAGES`，用完时在出处注明可能未读到）。
- **材料清点**：按上传页律师确认的文件类型，由代码判定齐全与否（`services/materials.py`，与上传接口、前端 `lib/materials.ts` 同一口径），不调 LLM。缺材料不终止、不暂停：律师在上传页已看到缺什么，点了「材料不全，仍然分析」就照常继续，缺的字段留【待补充】，校验面板琥珀色提醒（`materials` 检查）。
- **interrupt 断点**：字段冲突、需联网确认名称时，agent 通过 LangGraph `interrupt` 暂停，把决策抛到前端审核页；律师给出选择后 `resume` 继续。Step 3 审核页即此断点的 UI 呈现，支持多次往返。冲突断点附各字段的候选值与来源（`pending.field_options`，`services/conflict_options.py`）：金额勾稽给出三项现值及用另两项推出的值（有一项含「万 / 亿」等单位、不是纯数字写法时不推算），写进起诉状的台数给出各来源的台数及出处（各来源字段只给自身现值，不跨来源补），信用代码 / 日期给出现值与出处；律师点选或自填。断点节点内不放 OCR / LLM 等昂贵或有副作用的操作：resume 时 LangGraph 会从头重跑被打断的节点（`contract_scan` 与 `qty_check` 分开即为此）。

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
前端 html-doc-generator 生成 .doc → 下载
```

---

## 关于 agent 框架的取舍（①）

- **不引入 LangChain 本体**：抽象层深、prompt 不透明、版本易 breaking，对"可审计"要求高的法律场景是负担。结构化输出、重试等零件自行实现或用轻量库。
- **采用 LangGraph 的图 + checkpoint + interrupt**：正好承载"抽取→发现冲突→定位重读→暂停问律师→恢复"的控制流。checkpoint 存 SQLite（`agent/checkpoint.py`）；未来接 PostgreSQL 时替换 saver 即可，零重构。
- **工具层与校验层保持框架无关**：所有工具（解析、OCR、校验、查询、渲染）是纯 Python 函数/service，不依赖 LangGraph，便于单测与替换编排器。

## 为什么用 Python 后端

- OCR（Qwen-VL-OCR / RapidOCR）需在 Python 端调度，浏览器无法跑
- 文件解析（PDF/Word）在后端更稳定，库更成熟
- agent 循环、确定性校验、工具编排都在后端；前端只负责 UI 与律师交互
