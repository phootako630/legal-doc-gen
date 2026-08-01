# 配置中心：从 .env 读取所有环境变量和运行时常量
import os
from dotenv import load_dotenv

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 8192
LLM_TIMEOUT = 120  # 秒

# ── OCR ──────────────────────────────────────────────────────────────────────
# 当前实现：Qwen-VL-OCR API（云端，无需本地模型）
# Benchmark 备用：RapidOCR（本地 ONNX，见 ocr_engine.py 注释区）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
QWEN_OCR_MODEL = "qwen-vl-ocr-latest"

# 启动时打印密钥诊断（仅显示首尾4位，保护安全）
if DASHSCOPE_API_KEY:
    _k = DASHSCOPE_API_KEY.strip()
    print(f"[CONFIG] DASHSCOPE_API_KEY loaded: {_k[:8]}...{_k[-4:]} (len={len(_k)})", flush=True)
else:
    print("[CONFIG] WARNING: DASHSCOPE_API_KEY not set!", flush=True)

OCR_TIMEOUT = 120  # 秒（云端 API，比本地模型快，60s 已足够单页）

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50

# CORS 允许的前端地址
CORS_ORIGINS = ["http://localhost:5173"]
