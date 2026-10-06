#!/bin/bash
# 按端口停掉后端（8000）和前端（5173）。
# 不要用 pkill -f <脚本名>：它会匹配到调用它的 shell 本身，把当前命令一起杀掉（exit 144）。
for port in 8000 5173; do
  lsof -ti:"$port" -sTCP:LISTEN | xargs -r kill
done
echo "已停止 8000 / 5173"
