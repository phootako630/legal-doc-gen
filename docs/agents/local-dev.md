# 本地开发与 Claude Code 辅助

本地启动前后端、看 UI、用真实案卷端到端测试或改 `.claude/` 下的 hook / skill 时读本文件。

## 本地开发启动

```bash
# 1. 启动后端
cd backend
pip install -r requirements.txt
cp .env.example .env       # 填入 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 2. 启动前端
cd frontend
pnpm install
pnpm dev                   # 默认 http://localhost:5173

# 前端通过 vite.config.ts 的 proxy 将 /api/* 转发到 localhost:8000
```

## Claude Code 辅助（`.claude/`）

- `hooks/session-start.sh`：云端会话启动时安装后端 requirements 与前端依赖（与 CI 一致），ruff / pytest / ESLint / build 开箱可跑。云端容器闲置会被回收，scratchpad（含真实案卷）随之清空
- `skills/run-app`：启动前后端（数据隔离到临时目录），Playwright 走完四步截图，并保存分析结果与生成的诉状文本
- `skills/case-review`：律师发来新案卷时解压、盘点材料、看扫描页，用系统跑一遍并与律师诉状逐句对比
- `skills/lawyer-questionnaire`：把要问律师的问题生成为 Word 确认单（律师在中国大陆，确认单一律用 Word），并说明如何读取回复
