#!/bin/bash
# 云端会话（Claude Code on the web）启动时安装依赖，让后端 ruff / pytest、前端 lint / build 开箱可跑。
# 只装测试与 lint 需要的东西（与 .github/workflows/ci.yml 一致）；LibreOffice、unrar 等
# 只有个别 skill 用到的工具，由对应 skill 按需安装，不拖慢每次会话启动。
set -euo pipefail

# 本地开发不执行：本地环境由开发者自己管理
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# 后端：FastAPI / LangGraph / ruff / pytest 等（幂等，已装的跳过）
pip install --quiet --disable-pip-version-check --root-user-action=ignore -r backend/requirements.txt

# 前端：依赖 + ESLint；用 install 而非 ci，命中容器缓存时几乎不耗时
(cd frontend && pnpm install --prefer-offline --reporter=silent)
