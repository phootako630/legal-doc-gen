# Complaint Assistant · Elevator Installation-Contract Dispute Automation (v2)

[中文](./README.md) | **English**

A web app for legal teams: lawyers upload case materials (approval forms, installation contracts, acceptance/inspection reports), an agent automatically runs material check-in, field extraction, and cross-validation, and after the lawyer reviews and confirms, generates a civil complaint as a Word document with one click.

> **v1 → v2**: v1 was a fixed pipeline (`upload → checklist → extract → validate → generate`, hard-wired control flow). v2 makes the core **agentic** — a tool-equipped LangGraph agent drives control flow, reads materials on demand, self-checks with deterministic code, and pauses at key disagreements to ask the lawyer (human-in-the-loop).
>
> v2 scope: validate AI extraction accuracy and lawyer UX. No database, no auth, cases are not persisted (agent state lives only within a single session), local run only.

---

## Core principles

- **AI never makes the final call** — the agent only extracts, validates, and proposes; the lawyer confirms and edits. It pauses at key disagreements to ask.
- **Validation is code, not LLM** — amount reconciliation, three-source quantity comparison, and format checks are all done by deterministic Python; the LLM only "pulls out the numbers".
- **Traceable provenance** — every field value is tagged with its source file plus a **verified real page number** (derived by per-page anchoring, not self-reported by the LLM).
- **Prefer a gap over a fabrication** — uncertain fields are marked missing, never invented; a value that fails to anchor back to the source is down-weighted and flagged "to verify".
- **Template rendering, not free generation** — the complaint is rendered deterministically from structured fields + a template; the LLM does not write the full text.

---

## Tech stack

| | |
|---|---|
| Frontend | React 19 + Vite + TypeScript (strict) + Tailwind CSS + shadcn/ui + react-hook-form/zod + docx-js |
| Backend | FastAPI (Python 3.10+) + PyMuPDF (PDF) + python-docx (Word) |
| Agent orchestration | LangGraph (state graph + `interrupt` breakpoints + in-memory checkpoint; graph & checkpoints only, no full LangChain) |
| OCR | Qwen-VL-OCR (Alibaba Cloud DashScope API) for scanned documents |
| LLM | DeepSeek API (`deepseek-v4-flash`, via the OpenAI SDK with structured output) |

---

## Material types & source reliability tiers

A typical case has three material types that differ greatly in form and reliability; the agent treats them differently:

| Material | Form | Reliability | Agent strategy |
|---|---|---|---|
| **Approval form** | Parseable text, key-value form | ⭐⭐⭐ Highest | **Primary source**: defendant name/address, contract no., quantities, amounts, most fields taken directly |
| **Acceptance/inspection report** | Parseable text + stamp images | ⭐⭐ Medium-high | **Cross-check source**: USCC code, install site, acceptance date, quantity (number of reports) |
| **Installation contract** | Often pure scan (needs OCR) | ⭐ Noisy | **Clause-text source**: payment / penalty-interest / dispute-resolution clauses. OCR only the relevant pages on demand |

---

## Architecture

```mermaid
flowchart TB
    subgraph Browser["Browser · React SPA (single-page Stepper)"]
        S1["Step 1 Upload"] --> S2["Step 2 Processing"] --> S3["Step 3 Review"] --> S4["Step 4 Preview & download"]
        S4 --> DOCX["html-doc-generator (docx-js)<br/>template-filled Word in the browser"]
    end

    subgraph Backend["FastAPI backend (local uvicorn, no DB)"]
        RU["POST /api/upload"]
        RA["POST /api/analyze (start agent, may pause)"]
        RR["POST /api/resume (resume at breakpoint)"]
        RG["POST /api/generate (template render)"]

        subgraph Agent["LangGraph state graph"]
            N1["intake"] --> N2["extract"] --> N3["validate<br/>(interrupt on conflict)"]
        end

        subgraph Tools["services/ pure-function tools (framework-agnostic)"]
            FP["file_parser: parse + per-page text"]
            OCR["ocr_engine: Qwen-VL-OCR"]
            VAL["validators: deterministic checks"]
            CONF["confidence: layered scoring"]
            ANC["anchoring: value-to-source match"]
            PROV["provenance: per-page location"]
            REND["complaint_renderer: template fill"]
        end

        RU --> FP
        RA --> Agent
        RR --> Agent
        N2 --> ANC & PROV
        N2 -->|missing clauses · on demand| OCR
        N3 --> VAL & CONF
        RG --> REND
    end

    subgraph External["External services"]
        DS["DeepSeek API"]
        QW["DashScope · Qwen-VL-OCR"]
    end

    S1 -->|multipart/form-data| RU
    RU -->|files + text + pages| S2
    S2 -->|analyze| RA
    RA -->|CaseState (with pending)| S3
    S3 -->|resume decisions| RR
    RR -->|CaseState| S3
    S3 -->|validated_state| RG
    RG -->|complaint_text| S4

    N2 -.-> DS
    OCR -.-> QW
```

---

## Data flow (agent session + human-in-the-loop)

```mermaid
sequenceDiagram
    actor Lawyer
    participant FE as Frontend SPA
    participant BE as FastAPI + LangGraph
    participant CODE as Deterministic checks (code)
    participant LLM as DeepSeek API

    Lawyer->>FE: Drag-and-drop PDF / Word
    FE->>BE: POST /api/upload
    Note over BE: PyMuPDF / python-docx extract;<br/>keep per-page text; mark is_scanned
    BE-->>FE: files[] (text / pages / type / is_scanned)

    FE->>BE: POST /api/analyze
    BE->>LLM: intake (materials complete?)
    alt Materials insufficient
        BE-->>FE: CaseState.pending = missing (stop, prompt for more)
    else Materials complete
        BE->>LLM: extract (structured JSON)
        BE->>BE: anchor values per page -> real page + confidence
        BE->>CODE: validate (amount reconcile / 3-source qty / USCC / date)
        alt Conflict
            BE-->>FE: CaseState.pending = conflict (interrupt)
            Lawyer->>FE: resolve each field
            FE->>BE: POST /api/resume (decisions)
            BE->>CODE: re-check after applying (multi-round)
        end
        BE->>LLM: produce highlight prose from deterministic verdicts
        BE-->>FE: CaseState (fields + checks + readiness, pending=null)
    end

    FE->>Lawyer: Field table (source / page / status / readiness)
    Lawyer->>FE: Edit / confirm, then generate
    FE->>BE: POST /api/generate (validated_state)
    BE->>BE: complaint_renderer template fill (no LLM)
    BE-->>FE: complaint_text
    FE-->>Lawyer: docx-js builds Word for download
```

---

## Deterministic validation & confidence layering

- **Validation (code, not LLM)**: `validators.py` does amount reconciliation (`total == paid + unpaid`), three-source quantity consistency (signed/contract/acceptance), 18-digit USCC check digit, and date validity. Its verdicts are the sole authority for the "conflict" status on the review page.
- **Confidence layering**: hard signals set the status (`FieldStatus`: normal / missing / conflict / to-verify); soft signals set `confidence` (0–100, only for UI ordering/default-expansion). `confidence = channel×40 + anchor quality×30 + self-consistency×20 + model self-rating×10`.
- **Traceable provenance**: `anchoring.py` matches the extracted value back to the source text page by page; `provenance.py` returns the **verified real page number** from the hit. A miss never fabricates a page — it only lowers confidence and flags "to verify".

---

## Project structure

```
legal-doc-gen/
├── CLAUDE.md                        # AI coding-assistant project instructions (detailed spec)
├── README.md / README.en.md         # This file (zh / en)
├── prompts/                         # LLM prompt templates
│   ├── prompt-a-checklist.md        # Material check-in
│   ├── prompt-a-extract.md          # Field extraction (structured)
│   ├── prompt-a-validate.md         # Highlight prose only (no consistency judgment)
│   └── complaint-template.md        # Complaint template (placeholders)
│
├── frontend/                        # React frontend
│   └── src/
│       ├── components/{upload,processing,review,preview,layout,ui}/
│       ├── hooks/useComplaintFlow.ts        # analyze/resume flow state
│       └── lib/{api,buildReviewFields,field-map,html-doc-generator,types}.ts
│
└── backend/                         # Python FastAPI backend
    └── app/
        ├── main.py
        ├── routers/{files,analyze,resume,generate,extract}.py   # extract is v1 legacy
        ├── agent/{graph,state,nodes}.py                          # LangGraph state graph
        ├── services/                                             # framework-agnostic pure tools
        │   ├── file_parser.py  ocr_engine.py  llm_client.py  prompt_loader.py
        │   ├── validators.py   confidence.py  anchoring.py   provenance.py
        │   ├── extraction.py   complaint_renderer.py
        │   └── llm_progress.py upload_progress.py
        └── config.py
```

---

## Local development

```bash
# 1. Start the backend
cd backend
pip install -r requirements.txt
cp .env.example .env       # fill in DEEPSEEK_API_KEY and DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 2. Start the frontend
cd frontend
pnpm install
pnpm dev                   # http://localhost:5173, /api/* proxied to :8000
```

Backend tests: `cd backend && python -m pytest`

---

## Migration progress (v1 pipeline → v2 agentic)

| Step | Content | Status |
|---|---|---|
| 1–2 | Tool layer (validators / confidence / anchoring) + unit tests | ✅ |
| 3 | Move cross-validation out of the LLM into deterministic code | ✅ |
| 4 | Render the complaint via template fill (drop LLM free-gen) | ✅ |
| 5 | Agent orchestration (LangGraph + interrupt, backend & frontend) | ✅ |
| 6a | Field provenance (verified page numbers via per-page anchoring) | ✅ |
| 6b | On-demand OCR (page-by-page `ocr_page` + `search_in_docs` on scanned contracts when clauses are missing, early-stop) | ✅ |
| 7 | Entity verification (branch name → full legal name): HITL lawyer lookup-and-enter, online lookup API reserved | ✅ |

---

## Not included in v2 (reserved for later)

Database persistence, user auth, batch case processing, Dockerized deployment, vector retrieval, full LangChain, etc. — see [CLAUDE.md](./CLAUDE.md).
