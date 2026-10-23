#!/usr/bin/env bash
# 服务器端部署脚本：把代码更新到指定提交 → 重新构建并启动 → 检查健康 → 失败自动回滚到上一个版本。
#
# 两种用法：
#   1. GitHub Actions 自动部署（.github/workflows/deploy.yml）：用专用部署密钥 SSH 登录，
#      authorized_keys 里把这把钥匙强制绑定到本脚本（command="…/deploy/deploy.sh"），
#      要部署的提交号经 SSH_ORIGINAL_COMMAND 传入；这把钥匙只能跑本脚本，拿不到 shell。
#   2. 手动：在仓库根目录执行 deploy/deploy.sh [提交号]，不写提交号即部署 origin/master 最新版。
#
# 只部署已合入 origin/master 的提交；服务器上有人改过受版本控制的文件时拒绝部署（不覆盖手工修改）。
# .env、backend/.env、证书、数据卷都不受影响（不进 git 或在 Docker 卷里）。
set -euo pipefail

# git 会改写本脚本自身；bash 边读边执行，先复制一份再执行，避免执行到一半文件被换掉。
# 仓库路径要在复制前算好传下去（复制件在临时目录里）
if [ -z "${DEPLOY_SCRIPT_COPY:-}" ]; then
  tmp="$(mktemp)"
  cp "$0" "$tmp"
  DEPLOY_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)" DEPLOY_SCRIPT_COPY="$tmp" exec bash "$tmp" "$@"
fi
REPO_DIR="$DEPLOY_REPO_DIR"
trap 'rm -f "$DEPLOY_SCRIPT_COPY"' EXIT

cd "$REPO_DIR"

# 同一时间只允许一次部署
exec 9>"${TMPDIR:-/tmp}/legal-doc-deploy.lock"
if ! flock -n 9; then
  echo "已有部署在进行，本次退出" >&2
  exit 1
fi

target="${SSH_ORIGINAL_COMMAND:-${1:-}}"
if [ -n "$target" ] && ! printf '%s' "$target" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "提交号格式不对（需要 40 位十六进制）：$target" >&2
  exit 1
fi

echo "== 拉取最新代码"
git fetch --quiet origin master
target="${target:-$(git rev-parse origin/master)}"
if ! git merge-base --is-ancestor "$target" origin/master; then
  echo "提交 $target 不在 origin/master 上，拒绝部署" >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "服务器上有人改过受版本控制的文件，为免覆盖，拒绝部署：" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

previous="$(git rev-parse HEAD)"
echo "== 当前版本 ${previous:0:7}，部署版本 ${target:0:7}"

# 读根目录 .env（docker compose 参数）里的值，没有则用默认
env_value() {
  local v
  v="$(grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  printf '%s' "${v:-$2}"
}

# 检查健康：后端 healthy + 经 nginx 能访问登录状态接口
check() {
  local mode url
  mode="$(env_value NGINX_MODE http)"
  if [ "$mode" = "https" ]; then
    url="https://127.0.0.1:$(env_value HTTPS_PORT 443)/api/auth/me"
  else
    url="http://127.0.0.1:$(env_value HTTP_PORT 80)/api/auth/me"
  fi
  for _ in $(seq 1 20); do
    # -k：证书签给域名，本机用 IP 访问只验证服务通不通
    if curl -fsk --max-time 5 "$url" | grep -q '"auth_enabled"'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# 等后端正在执行的分析 / 断点恢复结束再重启，免得律师的分析中途被打断（GET /busy，只在容器内部可访问）。
# 最多等 DEPLOY_DRAIN_TIMEOUT 秒（默认 600，一次分析通常 2–3 分钟）；期间又有人开始分析也只等到超时为止。
# 后端没在运行（首次部署、回滚时已挂掉）或查不到时直接继续
wait_idle() {
  local timeout="${DEPLOY_DRAIN_TIMEOUT:-600}" waited=0 running
  while :; do
    running="$(docker compose exec -T backend python -c \
      "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/busy', timeout=4))['running'])" \
      2>/dev/null || echo 0)"
    if ! [ "$running" -gt 0 ] 2>/dev/null; then
      return 0
    fi
    if [ "$waited" -ge "$timeout" ]; then
      echo "!! 等了 ${timeout} 秒仍有 ${running} 个分析在进行，继续重启（这些分析会中断，律师重新点「开始分析」即可）" >&2
      return 0
    fi
    echo "   有 ${running} 个分析正在进行，等它结束再重启（已等 ${waited} 秒）"
    sleep 10
    waited=$((waited + 10))
  done
}

# 先构建新镜像（旧服务照常运行），再等分析结束，最后才替换容器，律师感受到的停机只有重启那几秒
up() {
  docker compose build &&
    wait_idle &&
    docker compose up -d --wait --wait-timeout 300
}

git reset --quiet --hard "$target"
echo "== 构建新版本（约几分钟，期间服务照常可用），等进行中的分析结束后重启"
if up && check; then
  echo "== 部署成功：${target:0:7}"
  docker image prune -f >/dev/null || true
  exit 0
fi

echo "!! 新版本启动或检查失败，回滚到 ${previous:0:7}" >&2
docker compose logs --tail 60 backend >&2 || true
git reset --quiet --hard "$previous"
if up && check; then
  echo "!! 已回滚到 ${previous:0:7}，服务正常；请修复后重新部署" >&2
else
  echo "!! 回滚后服务仍不正常，请登录服务器排查：docker compose ps / docker compose logs backend" >&2
fi
exit 1
