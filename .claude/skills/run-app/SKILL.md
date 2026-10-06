---
name: run-app
description: 启动起诉状系统（FastAPI 后端 + Vite 前端），用 Playwright 走完 上传 → AI 处理 → 审核 → 预览 四步并截图，保存分析结果与生成的诉状文本。用于给用户看 UI、验证改动在真实页面上的效果、用真实案卷做端到端测试。
---

# 启动并实测系统

本 skill 把「启动 → 上传材料 → 等分析 → 审核页 → 预览」固定成两条命令，并记录了在云端容器里踩过的坑。

## 前置条件

- 依赖：云端会话由 `.claude/hooks/session-start.sh` 自动安装（后端 requirements、前端 pnpm）。本地运行需自行安装。
- API 密钥：`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY` 需在环境变量或 `backend/.env` 中。缺了也能启动和看页面，但「开始分析」会失败。检查时只看是否设置，**不要打印取值**：
  ```bash
  for k in DEEPSEEK_API_KEY DASHSCOPE_API_KEY; do [ -n "${!k:-}" ] && echo "$k 已设置" || echo "$k 未设置"; done
  ```
- 注意 `config.py` 用 `load_dotenv(override=True)`：`backend/.env` 里的同名变量会覆盖命令行传入的环境变量。

## 步骤

以下 `$SKILL` 指本目录 `.claude/skills/run-app`，`$SCRATCH` 指会话的 scratchpad 目录。

1. **启动**（数据全部隔离到临时目录，不写 `backend/data/`）：
   ```bash
   $SKILL/scripts/start.sh "$SCRATCH/app-data"
   ```
   管理员口令默认 `test-admin`（可用 `ADMIN_TOKEN=... start.sh ...` 覆盖），用于测试「原告信息表」页面。

2. **驱动页面**。分析一次约 2–3 分钟，Bash 工具单次上限 10 分钟，务必 `run_in_background: true`，完成后会收到通知：
   ```bash
   export LC_ALL=C.UTF-8
   node $SKILL/scripts/drive.cjs "$SCRATCH/run-1" "$SCRATCH/case/诉讼审批表.pdf" "$SCRATCH/case/电梯安装合同.pdf" "$SCRATCH/case/电梯监督检验报告.pdf"
   ```
   - 遇到断点（律师确认弹窗）默认截图后停止；加 `--resume` 则按页面默认值提交并继续。
   - 输出：`01-upload.png` … `06-preview-full.png`、`04-pending-N.png`、`analyze.json`（字段与校验结果）、`complaint.txt`（生成的诉状全文，已去掉填空标记）。

3. **看截图**：用 Read 打开 png 逐张检查。空白页 = 没启动成功。把需要给用户看的截图用 SendUserFile 发出。

4. **停止**：
   ```bash
   $SKILL/scripts/stop.sh
   ```

## 踩过的坑

- **上传必须带 MIME 类型**：Playwright 只给文件路径时，中文文件名的 `File.type` 可能为空，前端会以「格式不支持」拒收，文件列表不出现。`drive.cjs` 已用 buffer + 显式 `mimeType` 处理。
- **文件名影响类型识别**：上传前把文件复制成含「审批表」「合同」「检验报告」字样的名字，材料清点更稳。
- **中文路径要 UTF-8**：shell 默认 locale 下中文文件名会变成 `????`。命令前加 `export LC_ALL=C.UTF-8`。
- **停服务按端口杀**：`pkill -f drive.cjs` 这类写法会匹配到发起命令的 shell 本身，把当前命令一起杀掉（exit 144）。用 `stop.sh`。
- **改了后端代码要重启**：`start.sh` 不带 `--reload`，会先停旧进程再启动。
- **分析失败（HTTP 500）**：路由把异常转成 `{"detail": "分析失败：…"}`，后端日志里没有 traceback。看 `analyze.json` 或直接 curl 接口拿 `detail`。
- **整页截图中间有一条页头**：页头是 sticky，`fullPage` 截图拼接时会出现在中间，属截图产物，不是页面问题。
- **截图目录别传错**：`drive.cjs` 第一个参数就是输出目录，传成 scratchpad 根目录会把截图散落在根目录下。

## 隐私

真实案卷含个人和企业信息：只放 scratchpad，绝不提交到仓库。截图、`analyze.json`、`complaint.txt` 同样只留在 scratchpad。
