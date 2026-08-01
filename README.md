# 起诉状助手 · 安装合同纠纷起诉状自动化系统（v1）

面向律师团队的 Web 应用：上传案件材料（审批表、合同、验收报告等），由 AI 完成信息抽取与交叉校验，律师审核确认后一键生成民事起诉状 Word 文档。

> v1 定位：验证 AI 抽取准确率与律师使用体验。无数据库、无用户认证，案件不持久化，仅本地运行。

---

## 核心原则

- **AI 不做最终判断**——AI 只负责抽取和校验，律师负责确认与修改
- **出处可追溯**——每个字段值都标注来源文件和位置
- **宁缺勿造**——无法确定的字段标为缺失，绝不编造

---

## 技术栈

| | |
|---|---|
| 前端 | React 19 + Vite + TypeScript（strict）+ Tailwind CSS + shadcn/ui + react-hook-form/zod + docx-js |
| 后端 | FastAPI（Python 3.13）+ PyMuPDF（PDF）+ python-docx（Word） |
| OCR | Qwen-VL-OCR（阿里云 DashScope 云端 API），扫描件识别 |
| LLM | DeepSeek API（`deepseek-chat`，通过 OpenAI SDK 调用） |

---

## 架构图

```mermaid
flowchart TB
    subgraph Browser["浏览器"]
        UI["React SPA（单页 Stepper）"]
        S1["Step 1 上传文件"]
        S2["Step 2 AI 处理中"]
        S3["Step 3 审核确认"]
        S4["Step 4 预览下载"]
        DOCX["docx-js<br/>浏览器端生成 Word"]
        UI --> S1 --> S2 --> S3 --> S4
        S4 --> DOCX
    end

    subgraph Backend["FastAPI 后端（本地 uvicorn，无数据库）"]
        R1["POST /api/upload"]
        R2["POST /api/extract"]
        R3["POST /api/generate"]

        FP["file_parser.py<br/>PDF / Word 文本提取调度"]
        OCR["ocr_engine.py<br/>Qwen-VL-OCR 封装"]
        LLM["llm_client.py<br/>DeepSeek API 封装"]
        PL["prompt_loader.py<br/>读取 prompts/ 注入变量"]

        R1 --> FP
        FP -->|扫描件| OCR
        R2 --> PL --> LLM
        R3 --> PL
    end

    subgraph External["外部服务"]
        DS["DeepSeek API<br/>deepseek-chat"]
        QW["阿里云 DashScope<br/>Qwen-VL-OCR"]
    end

    subgraph Prompts["prompts/ 目录"]
        PA1["prompt-a-checklist.md<br/>材料清点"]
        PA2["prompt-a-extract.md<br/>字段抽取"]
        PA3["prompt-a-validate.md<br/>校验高亮"]
        PB["prompt-b-generate.md<br/>起诉状生成"]
        TPL["complaint-template.md<br/>模板常量"]
    end

    S1 -->|multipart/form-data| R1
    R1 -->|files + text + 类型| S2
    S2 -->|files_text, internet_allowed| R2
    R2 -->|extracted_fields + validation_report + highlight_list| S3
    S3 -->|validated_json（律师确认后）| R3
    R3 -->|complaint_text| S4

    OCR -.-> QW
    LLM -.-> DS
    PL --> PA1 & PA2 & PA3 & PB & TPL
```

---

## 数据流（三步处理）

```mermaid
sequenceDiagram
    actor 律师
    participant FE as 前端 SPA
    participant BE as FastAPI 后端
    participant OCR as Qwen-VL-OCR
    participant LLM as DeepSeek API

    律师->>FE: 拖拽上传 PDF / Word
    FE->>BE: POST /api/upload
    alt PDF 可解析文字
        BE->>BE: PyMuPDF 直接提取
    else PDF 为扫描件
        BE->>OCR: 图片识别
        OCR-->>BE: 识别文本
    else Word 文件
        BE->>BE: python-docx 提取
    end
    BE-->>FE: files[]（text / 类型 / 是否扫描件）

    FE->>BE: POST /api/extract（files_text, internet_allowed）
    BE->>LLM: Prompt A-1 材料清点
    LLM-->>BE: MaterialChecklist（can_proceed?）
    BE->>LLM: Prompt A-2 字段抽取
    LLM-->>BE: ExtractedFields JSON
    BE->>LLM: Prompt A-3 校验高亮
    LLM-->>BE: 校验报告 + 高亮列表
    BE-->>FE: extracted_fields + validation_report + highlight_list

    FE->>律师: 渲染字段表格（中文，含来源/状态标签）
    律师->>FE: 编辑 / 确认字段（冲突项人工选择）

    FE->>BE: POST /api/generate（validated_json）
    BE->>LLM: Prompt B + 模板
    LLM-->>BE: 起诉状全文
    BE-->>FE: complaint_text

    FE->>FE: docx-js 生成 Word 文档
    FE-->>律师: 下载起诉状 Word
```

---

## 项目结构

```
legal-doc-gen/
├── CLAUDE.md                        # AI 编码助手项目指令（详细规范）
├── prompts/                         # LLM Prompt 模板
│   ├── prompt-a-checklist.md
│   ├── prompt-a-extract.md
│   ├── prompt-a-validate.md
│   ├── prompt-b-generate.md
│   └── complaint-template.md
│
├── frontend/                        # React 前端
│   └── src/
│       ├── components/
│       │   ├── ui/                  # shadcn/ui
│       │   ├── layout/              # Header, Stepper
│       │   ├── upload/              # Step 1
│       │   ├── processing/          # Step 2
│       │   ├── review/              # Step 3（核心）
│       │   └── preview/             # Step 4
│       ├── hooks/useComplaintFlow.ts
│       └── lib/{api,doc-generator,field-map,types}.ts
│
└── backend/                         # Python FastAPI 后端
    └── app/
        ├── routers/{files,extract,generate}.py
        ├── services/{file_parser,ocr_engine,llm_client,prompt_loader}.py
        └── config.py
```

---

## 本地开发

```bash
# 1. 启动后端
cd backend
pip install -r requirements.txt
cp .env.example .env       # 填入 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 2. 启动前端
cd frontend
pnpm install
pnpm dev                   # http://localhost:5173，/api/* 代理转发到 :8000
```

---

## v1 不包含

数据库持久化、用户认证、批量案件处理、Docker 化部署等——详见 [CLAUDE.md](./CLAUDE.md#v1-不包含后续迭代预留)。
