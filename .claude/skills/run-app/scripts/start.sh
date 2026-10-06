#!/bin/bash
# 启动后端（8000）+ 前端（5173），所有运行时数据隔离到 $1 指定的临时目录，不碰 backend/data/。
# 用法：start.sh <数据目录>   例：start.sh "$SCRATCH/app-data"
set -euo pipefail
DATA="${1:?用法：start.sh <数据目录（放在 scratchpad 下）>}"
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
mkdir -p "$DATA"
export LC_ALL=C.UTF-8 LANG=C.UTF-8

# 已在运行就先停掉，保证用的是当前代码
"$(dirname "$0")/stop.sh" >/dev/null 2>&1 || true

# 测试数据全部指向 $DATA：原告信息表、历史版本、checkpoint、上传文件、运行日志
(
  cd "$ROOT/backend"
  BRANCH_INFO_PATH="$DATA/branch_info.json" \
  BRANCH_HISTORY_DIR="$DATA/branch_history" \
  CHECKPOINT_DB_PATH="$DATA/checkpoints.sqlite" \
  UPLOAD_DIR="$DATA/uploads" \
  RUN_LOG_PATH="$DATA/run_log.jsonl" \
  ADMIN_TOKEN="${ADMIN_TOKEN:-test-admin}" \
  nohup uvicorn app.main:app --port 8000 >"$DATA/backend.log" 2>&1 &
)
(
  cd "$ROOT/frontend"
  nohup pnpm dev --port 5173 --strictPort >"$DATA/frontend.log" 2>&1 &
)

for _ in $(seq 1 40); do
  if curl -s -o /dev/null localhost:8000/ && curl -s -o /dev/null localhost:5173/; then
    echo "后端 http://localhost:8000  前端 http://localhost:5173  数据目录 $DATA"
    echo "日志：$DATA/backend.log  $DATA/frontend.log"
    exit 0
  fi
  sleep 1
done
echo "启动超时，查看日志：$DATA/backend.log  $DATA/frontend.log" >&2
exit 1
