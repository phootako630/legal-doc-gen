# 配置中心：从 .env 读取所有环境变量和运行时常量
import os
from dotenv import load_dotenv

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

LLM_TEMPERATURE = 0.1
# deepseek-v4-flash 是推理模型：思维链（reasoning_content）也计入 max_tokens。
# 8192 时曾出现思维链耗尽全部配额、正文为空（finish_reason=length）——
# 必须留出足够余量，实测 16384 可被 API 接受
LLM_MAX_TOKENS = 16384
LLM_TIMEOUT = 120  # 秒

# ── OCR ──────────────────────────────────────────────────────────────────────
# 当前实现：Qwen-VL-OCR API（云端，无需本地模型）
# Benchmark 备用：RapidOCR（本地 ONNX，见 ocr_engine.py 注释区）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
QWEN_OCR_MODEL = "qwen-vl-ocr"

# 启动时打印密钥诊断（仅显示首尾4位，保护安全）
if DASHSCOPE_API_KEY:
    _k = DASHSCOPE_API_KEY.strip()
    print(
        f"[CONFIG] DASHSCOPE_API_KEY loaded: {_k[:8]}...{_k[-4:]} (len={len(_k)})",
        flush=True,
    )
else:
    print("[CONFIG] WARNING: DASHSCOPE_API_KEY not set!", flush=True)

OCR_TIMEOUT = 300  # 秒——整份文件（所有页并发处理）的总超时，不是单页超时
OCR_PAGE_TIMEOUT = 60  # 秒——单页请求的超时（原先误用整份文件的超时给单页，太宽松）
OCR_MAX_CONCURRENCY = 5  # 单份文件内并发 OCR 请求数上限，避免触发 DashScope 限流

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50

# Agent 按需 OCR 护栏：单次分析对扫描件逐页 OCR 的页数上限（成本上限）。
# 命中付款/违约/争议三条款即提前停止，通常远不到此上限。
AGENT_MAX_OCR_PAGES = 20

# CORS 允许的前端地址
CORS_ORIGINS = ["http://localhost:5173"]
