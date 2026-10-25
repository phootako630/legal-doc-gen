# 配置中心：从 .env 读取所有环境变量和运行时常量
import json
import logging
import os
from dotenv import load_dotenv

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# 2026-09-10 起官方模型名为 deepseek-flash（V4.1 Flash）；旧名 deepseek-v4-flash 只是临时别名，
# 官方未公布下线日期，故改用正式名。需要临时切换时在 .env 里设 DEEPSEEK_MODEL 覆盖。
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

LLM_TEMPERATURE = 0.1
# DeepSeek Flash 是推理模型：思维链（reasoning_content）也计入 max_tokens。
# 8192 时曾出现思维链耗尽全部配额、正文为空（finish_reason=length）——
# 必须留出足够余量，实测 16384 可被 API 接受
LLM_MAX_TOKENS = 16384
LLM_TIMEOUT = 120  # 秒——SDK 的读取超时：两次收到数据之间的最长间隔
# 单次调用的总时限。DeepSeek 排队时会持续回空行保活，读取超时因此永远不触发（2026-10 宕机时实测
# 一次调用挂了 10 分钟以上）。正常抽取最长约 100 秒，超过总时限即按不可用处理、改用备用模型
LLM_TOTAL_TIMEOUT = 180  # 秒

# ── OCR ──────────────────────────────────────────────────────────────────────
# 当前实现：Qwen 多模态模型经 DashScope 云端 API 识别（无需本地模型）
# Benchmark 备用：RapidOCR（本地 ONNX，见 ocr_engine.py 注释区）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
# 付费备用 key：主 key（免费额度）被拒（HTTP 401/403，额度用完即 403）时自动改用，之后本次运行
# 一直用备用 key；限流（429）不切换。不配则只用主 key
DASHSCOPE_API_KEY_BACKUP = os.getenv("DASHSCOPE_API_KEY_BACKUP")
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# 默认 qwen3.8-flash（支持图片输入；实测扫描合同、手机拍照的检验报告都能完整识别表格，
# 价格远低于 qwen3.8-max）；可在 .env 用 QWEN_OCR_MODEL 覆盖，如 qwen3.8-max / qwen-vl-ocr。
QWEN_OCR_MODEL = os.getenv("QWEN_OCR_MODEL", "qwen3.8-flash")
# 关闭思考模式：OCR 只需照录原文，推理既增加耗时/费用，也可能让模型"纠正"原文（违背宁缺勿造）。
QWEN_OCR_ENABLE_THINKING = False

# 启动时记录密钥是否已配置（只显示末4位与长度，便于核对换没换 key，不泄露密钥）
_logger = logging.getLogger(__name__)
if DASHSCOPE_API_KEY:
    _k = DASHSCOPE_API_KEY.strip()
    _logger.info("DASHSCOPE_API_KEY 已配置：…%s（%d 位）", _k[-4:], len(_k))
else:
    _logger.warning("DASHSCOPE_API_KEY 未配置，扫描件无法识别")
if DASHSCOPE_API_KEY_BACKUP:
    _b = DASHSCOPE_API_KEY_BACKUP.strip()
    _logger.info("DASHSCOPE_API_KEY_BACKUP 已配置：…%s（%d 位）", _b[-4:], len(_b))

# ── LLM 备用模型 ─────────────────────────────────────────────────────────────
# DeepSeek 服务不可用（超时 / 连不上 / 5xx / 限流 429 / 余额不足 402）时改用通义千问，
# 走 DashScope 的 OpenAI 兼容接口，key 与 OCR 共用（主 key 被拒再用备用 key）。
# 设 LLM_FALLBACK_MODEL= （空）关闭；key 无效（401）属配置错误，不切换。
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "qwen3.8-flash")
LLM_FALLBACK_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
# 默认关闭思考：抽取是照原文取值，实测关闭后 1–5 秒返回；需要时 .env 设 1 打开
LLM_FALLBACK_ENABLE_THINKING = os.getenv("LLM_FALLBACK_ENABLE_THINKING", "0") == "1"
# DeepSeek 出错后这段时间内直接用备用模型，不再让每次调用都先等 DeepSeek 超时（单次最长 120 秒）
LLM_FALLBACK_COOLDOWN_S = 600

OCR_TIMEOUT = 300  # 秒——整份文件（所有页并发处理）的总超时，不是单页超时
OCR_PAGE_TIMEOUT = 60  # 秒——单页请求的超时（原先误用整份文件的超时给单页，太宽松）
OCR_MAX_CONCURRENCY = 5  # 单份文件内并发 OCR 请求数上限，避免触发 DashScope 限流
# 全服务同时在识别的页数上限（所有律师的所有分析共用，超出的排队）。OCR 用专用线程池，
# 不再挤占登录校验等请求共用的默认线程池（2 核服务器只有 6 个线程）。
# 依据：qwen3.8-flash 等级 1 限额 250 万 TPM，每页约 3,000–5,000 Token、每页 5–15 秒，
# 并发 8 约每分钟 30–100 页，远低于限额；10 位律师高峰同时分析也只是排队变慢，不被限流
OCR_GLOBAL_CONCURRENCY = int(os.getenv("OCR_GLOBAL_CONCURRENCY", "8"))

# 扫描件判断阈值：PyMuPDF 提取文字少于此字数 → 判定为扫描件
SCANNED_PDF_TEXT_THRESHOLD = 50
# 单页文字少于此字数 → 该页按扫描页处理。半数以上页是扫描页的 PDF 也按扫描件处理（有文字层的页照用、
# 不 OCR）：律师反馈的徐州案合同 101 页里 94 页、246 页里 138 页是扫描页，总字数却超过阈值
SCANNED_PAGE_TEXT_THRESHOLD = 20

# Agent 按需 OCR 护栏：单次分析对扫描件逐页 OCR 的页数上限（成本上限）。
# 找到付款 + 争议两类条款正文即提前停止。实测 45 页安装合同的付款条款在第 21 页，
# 原上限 20 页会恰好错过，故放宽到 50（覆盖常见安装合同全文）。
AGENT_MAX_OCR_PAGES = 50
# 扫描验收报告（常为手机拍照，每台一张首页）按需 OCR 的页数上限：取批准日期、安装地点、台数
AGENT_MAX_REPORT_OCR_PAGES = 30
# 扫描审批表（截图转 PDF，通常 1–5 页）分析开始时整份 OCR 的页数上限：金额、合同类型等主字段都在这里
AGENT_MAX_APPROVAL_OCR_PAGES = 8
# 扫描战略 / 框架合同另给的页数上限：主合同常把 50 页用完，战略合同（多为几页的补充协议）就一页也读不到
# （律师新案例：徐州九里苏宁买卖，101 页主合同 + 2 页战略协议补充协议）
AGENT_MAX_STRATEGIC_OCR_PAGES = 10
# 单次分析（含断点恢复后的继续）所有按需 OCR 合计的页数上限：审批表、合同、战略合同、验收报告、
# 补读设备清单共用，上面各项仍是各自的子上限（各项相加 98 页，不设总上限时最坏会全部用满）。
# 依据：11 个真实案卷每案 OCR 5–56 页（含上传时认标题的页），80 页全部覆盖并留有余量。
# 上传时认标题的首页不计入（发生在分析开始之前）。
AGENT_MAX_TOTAL_OCR_PAGES = 80

# ── 企业信息联网查询（预留）────────────────────────────────────────────────────
# 企查查 / 天眼查 / 国家企业信用信息公示系统查询 API 的密钥（暂未接入）。
# 未配置时 lookup_company 恒返回 None，agent 走 HITL 让律师自行查询键入法人全称。
COMPANY_LOOKUP_API_KEY = os.getenv("COMPANY_LOOKUP_API_KEY")

# ── 原告（律师确认的固定规则）────────────────────────────────────────────────
# 买卖合同原告为总公司；安装合同原告为「总公司全称 + 审批表『合同分公司』中的分公司名」
PLAINTIFF_HQ_NAME = "日立电梯（中国）有限公司"
# 第三轮确认单第 13 题：只有总公司在法定代表人姓名后写职务（「张三，董事长」）
PLAINTIFF_HQ_REP_TITLE = "董事长"
# 律师确认：原告电话一律用此号码
PLAINTIFF_PHONE = "0755-83679974"
# 原告信息表：总公司及各分公司的统一社会信用代码、法定代表人 / 负责人、住址。
# 管理员在前端「原告信息表」页面上传 Excel 维护，存在服务器本地（不进 git）。
# 文件不存在时不补这几项，起诉状留【待补充】。格式见 services/branch_table.py
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
BRANCH_INFO_PATH = os.getenv(
    "BRANCH_INFO_PATH", os.path.join(_DATA_DIR, "branch_info.json")
)
# 每次更新前把旧版本存到这里，可在页面上回退
BRANCH_HISTORY_DIR = os.getenv(
    "BRANCH_HISTORY_DIR", os.path.join(_DATA_DIR, "branch_history")
)
# 超过这么多天没更新，上传页提醒「负责人可能已变更」
BRANCH_TABLE_STALE_DAYS = 90

# ── 案件状态持久化（均在 backend/data/ 下，不进 git）────────────────────────────
# agent 的 checkpoint（含 interrupt 断点）存 SQLite，服务重启后律师仍可 resume；
# 扫描件原始字节落盘到 UPLOAD_DIR，供按需 OCR 取回。
CHECKPOINT_DB_PATH = os.getenv(
    "CHECKPOINT_DB_PATH", os.path.join(_DATA_DIR, "checkpoints.sqlite")
)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(_DATA_DIR, "uploads"))
# 案件数据（checkpoint 里的字段与原文、上传的原始文件）保留天数，超期自动清除。
# 这些是客户机密：保留期应由律所明确，此处先给保守默认值。
CASE_RETENTION_DAYS = int(os.getenv("CASE_RETENTION_DAYS", "7"))
# 就绪检查（/readyz）：数据目录所在磁盘剩余空间低于此值（MB）即未就绪。
# 一次上传最多约 95MB，checkpoint 与日志另需余量，故默认 500MB
READY_MIN_FREE_MB = int(os.getenv("READY_MIN_FREE_MB", "500"))

# 管理员口令：只有持口令者能上传 / 回退原告信息表（查看、下载不限）。
# 系统暂无用户账号体系，先用一个服务器端口令；未配置时任何人都不能更新。
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# ── 运行日志 ─────────────────────────────────────────────────────────────────
# agent 每次运行的结构化事件（JSONL，仅含字段 key / 耗时 / token，不含案件内容）。
# RUN_LOG_ENABLED=0 可关闭；日志在 backend/data/ 下，与原告信息表一样不进 git。
RUN_LOG_ENABLED = os.getenv("RUN_LOG_ENABLED", "1") != "0"
RUN_LOG_PATH = os.getenv("RUN_LOG_PATH", os.path.join(_DATA_DIR, "run_log.jsonl"))
# 按天切分，切分文件保留天数。日志不含案件内容（只有字段 key、耗时、token、账号名、run_id），
# 用于统计使用量与律师改动率，故比案件数据（CASE_RETENTION_DAYS，含原文）保留得久；
# run_id 在案件数据清除后就对不上任何案件。
RUN_LOG_RETENTION_DAYS = int(os.getenv("RUN_LOG_RETENTION_DAYS", "90"))

# 成本估算单价（人民币元），只给汇总脚本 scripts/cost_report.py 换算金额用，不影响运行。
# 以 DeepSeek / 阿里云百炼控制台的价格页为准；改价后同时改 COST_PRICES_DATE（取值日期）。
# 格式：{"模型名": {"input": 每百万输入 token, "output": 每百万输出 token}, "ocr_page": 每页 OCR}
# 未配置的模型 / OCR，报告里金额显示「未配置单价」，token 与页数照常统计
COST_PRICES: dict = json.loads(os.getenv("COST_PRICES") or "{}")
COST_PRICES_DATE = os.getenv("COST_PRICES_DATE", "")

# ── 每日运行小结（#76）────────────────────────────────────────────────────────
# 每天 DAILY_SUMMARY_HOUR 点（北京时间）后汇总前一天的运行日志，写到 DAILY_SUMMARY_DIR/YYYY-MM-DD.md
# （不含案件内容）；超过下面阈值时文件开头列出「需要关注」，并在后端日志里记一条警告。
# 以后要发邮件，在 services/daily_summary.deliver 里加通道即可。
DAILY_SUMMARY_ENABLED = os.getenv("DAILY_SUMMARY_ENABLED", "1") != "0"
DAILY_SUMMARY_DIR = os.getenv("DAILY_SUMMARY_DIR", os.path.join(_DATA_DIR, "daily_summary"))
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "8"))
# 一天内节点出错（分析中断）超过此次数
ALERT_MAX_NODE_ERRORS = int(os.getenv("ALERT_MAX_NODE_ERRORS", "3"))
# OCR 失败页占请求页的比例超过此值（当天请求不足 ALERT_MIN_OCR_PAGES 页时不判，免得一两页失败就报警）
ALERT_MAX_OCR_FAIL_RATE = float(os.getenv("ALERT_MAX_OCR_FAIL_RATE", "0.1"))
ALERT_MIN_OCR_PAGES = int(os.getenv("ALERT_MIN_OCR_PAGES", "20"))
# DeepSeek 不可用、改用备用模型超过此次数
ALERT_MAX_LLM_FALLBACKS = int(os.getenv("ALERT_MAX_LLM_FALLBACKS", "3"))
# 当天成本（元，按 COST_PRICES 换算）超过此值；0 = 不判
ALERT_MAX_DAILY_COST = float(os.getenv("ALERT_MAX_DAILY_COST", "0"))
# 付费备用 key 切换、Key 连通性检查失败：出现即列入「需要关注」，不设阈值

# ── 登录（账号 + 会话）─────────────────────────────────────────────────────────
# 部署时（docker-compose.yml）开启：除登录接口外所有 /api 都要求先登录。
# 本地开发与单测默认关闭，免得每次都要先建账号；关闭时启动日志会提醒。
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"
# 账号与会话库：与案件库分开，不随案件过期清除
AUTH_DB_PATH = os.getenv("AUTH_DB_PATH", os.path.join(_DATA_DIR, "auth.sqlite"))
SESSION_DAYS = 7  # 登录后免登录天数（到期须重新登录，不随使用顺延）
PASSWORD_MIN_LENGTH = 10
LOGIN_MAX_FAILURES = 5  # 同一 IP 对同一账号连续输错次数上限
LOGIN_MAX_FAILURES_PER_IP = 20  # 同一 IP 对所有账号累计输错上限（防换账号猜）
LOGIN_LOCK_MINUTES = 15

# CORS 允许的前端地址
CORS_ORIGINS = ["http://localhost:5173"]
