#!/bin/bash
# 云端会话（Claude Code on the web）启动时安装依赖，让后端 ruff / pytest、前端 lint / build 开箱可跑。
# 只装测试与 lint 需要的东西（与 .github/workflows/ci.yml 一致）；LibreOffice、unrar 等
# 只有个别 skill 用到的工具，由对应 skill 按需安装，不拖慢每次会话启动。
#
# 云端镜像里 `pip` 可能属于另一个 Python（曾出现 pip→3.13、python→3.11，依赖装进 3.13，
# python -m pytest 找不到包）；镜像还预装了别的版本的 ruff / pytest，在 PATH 里排在前面。
# 所以一律用 `python -m pip` 装进项目实际用的解释器，跑检查时用 `python -m ruff` / `python -m pytest`。
set -euo pipefail

# 本地开发不执行：本地环境由开发者自己管理
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# 后端：FastAPI / LangGraph / ruff / pytest 等（幂等，已装的跳过）
python -m pip install --quiet --disable-pip-version-check --root-user-action=ignore \
  -r backend/requirements.txt

# 让裸敲的 ruff / pytest 也用上面装的版本（锁定的 ruff 与 CI 一致）：把该解释器的脚本目录放到 PATH 最前
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  scripts_dir="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
  echo "export PATH=\"${scripts_dir}:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# 前端：依赖 + ESLint；用 install 而非 ci，命中容器缓存时几乎不耗时
(cd frontend && pnpm install --prefer-offline --reporter=silent)

# 自检：装错解释器或版本不对时在启动日志里一眼能看到
python --version
python -m ruff --version
python -m pytest --version
