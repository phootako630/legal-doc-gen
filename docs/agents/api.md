# API 路由

改后端路由、前后端接口、登录 / 会话隔离或部署探活接口时读本文件。路由实现在 `backend/app/routers/`。

## 登录与案件数据

- 登录：账号由管理员在服务器上用 `scripts/manage_users.py` 创建（无注册入口），会话 Cookie 7 天免登录，律师可自行修改密码；部署时强制开启（`AUTH_ENABLED=1`），本地开发与单测默认关闭。
- 案件状态（agent checkpoint、上传的扫描件）落盘到 `backend/data/`（SQLite + 文件），服务重启后仍可 resume；超过 `CASE_RETENTION_DAYS`（默认 7 天）自动清除（服务启动即清、之后每小时清，resume / 读取时也拒绝过期数据）。

## 路由

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
POST /api/case/{run_id}/page  { file, page, field }  # 审核页「原件第 N 页」：扫描件该页 PNG（#111）；文件名放请求体不放 URL（nginx / uvicorn 访问日志记请求行）；同上按账号隔离、过期 404，文件只在本会话材料里按名找（file_id 不经客户端），同名材料有多份时 404 不显示，文字版材料 404 并提示打开原文件；禁止缓存；渲染走专用线程（`services/page_image.py`）；运行日志 `source_page_view` 只记字段 key / 页码 / 结果，不记文件名
#   同名材料（#121）：上传接口给第 2 份起的同名文件加序号「安装合同(2).pdf」，响应 `renamed` 列出说明、上传页琥珀色提示；分析接口对直接调用的旧客户端 / eval 兜底同样编号（`services/file_names.py`）。会话内文件名因此唯一，出处、查看原件、来源锚定和 eval 来源核对都按新名称；升级前的会话若仍有同名材料，查看原件照旧 404「有多份同名材料」
GET /api/analyze/result?key=<请求编号>  # 处理页刷新后接回同一次分析：还在跑就等它跑完，已跑完直接返回；失败 / 重启 / 过期 404
GET /healthz、GET /readyz（不在 /api 下，nginx 不转发）  # 存活（docker 健康检查）/ 就绪（checkpoint 库读写、磁盘余量 `READY_MIN_FREE_MB`、Key 已配置；deploy.sh 用，不就绪回滚）
POST /api/client-error            # 前端上报未捕获异常 / 接口 5xx（#76）：只收错误类型、步骤、接口名、状态码、run_id（extra=forbid，不收消息原文），记运行日志 client_error，同一账号 / IP 每分钟 `CLIENT_ERROR_RATE_PER_MIN` 条；只在生产构建上报（`lib/error-report.ts`）
POST /drain、GET /busy（不在 /api 下，nginx 不转发）  # deploy.sh 重启前先 drain（新的分析 / 恢复 503「系统正在更新」），再等 busy 归零（含编号落盘）

POST /api/generate
  输入：{ run_id, validated_json }      # validated_json：律师审核后的字段
  处理：render_complaint（模板填槽，非 LLM 自由生成）
  输出：{ complaint_text }

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
