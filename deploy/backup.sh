#!/usr/bin/env bash
# 关键数据备份：原告信息表（当前版 + 历史版本）与账号库，存到服务器本机目录，按保留期自动清理。
#
# 用法（在仓库根目录）：
#   deploy/backup.sh                 # 备份一次，并删除超过保留期的旧备份
#   deploy/backup.sh verify <文件>   # 恢复演练：解压到临时目录逐项检查，不碰线上数据
# 定时执行见 deploy/README.md「备份与恢复」。
#
# 只备份 branch_info.json、branch_history/、auth.sqlite。案件数据（checkpoints.sqlite、uploads/）
# 按约定保留 CASE_RETENTION_DAYS（默认 7 天）后清除，备份会让它们留得更久，故不备份；运行日志
# 不含案件内容、丢了不影响使用，也不备份。账号库只保留账号（密码哈希），登录会话一律不带走
# （恢复后需要重新登录）。
#
# 可调：BACKUP_DIR（默认 /srv/legal-doc-backups，目录 0700、文件 0600）、BACKUP_RETENTION_DAYS（默认 30）
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-/srv/legal-doc-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
umask 077

# 在后端容器里打包，输出 tar.gz 到 stdout。路径取后端自己的配置（与运行时一致）；
# 账号库用 SQLite 在线备份接口复制（不停服务也一致），再删掉会话表
PACK_PY='
import os, sqlite3, sys, tarfile, tempfile
from app import config

with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as tar:
    if os.path.isfile(config.BRANCH_INFO_PATH):
        tar.add(config.BRANCH_INFO_PATH, arcname="branch_info.json")
    if os.path.isdir(config.BRANCH_HISTORY_DIR):
        tar.add(config.BRANCH_HISTORY_DIR, arcname="branch_history")
    if os.path.isfile(config.AUTH_DB_PATH):
        copy = os.path.join(tmp, "auth.sqlite")
        src = sqlite3.connect(f"file:{config.AUTH_DB_PATH}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        src.backup(dst)
        src.close()
        dst.execute("DELETE FROM sessions")
        dst.commit()
        dst.execute("VACUUM")
        dst.close()
        tar.add(copy, arcname="auth.sqlite")
'

# 恢复演练：解压到临时目录，检查原告信息表可读、账号库完整且有账号。只打印数量，不打印名称与内容
VERIFY_PY='
import json, os, sqlite3, sys, tarfile, tempfile

path = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if name.startswith(("/", "..")) or not (m.isfile() or m.isdir()):
                sys.exit(f"备份里有可疑路径，拒绝解压：{m.name}")
        tar.extractall(tmp)
    problems = []
    info = os.path.join(tmp, "branch_info.json")
    if os.path.isfile(info):
        with open(info, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("entries", []) if isinstance(data, dict) else data
        print(f"原告信息表：可读，{len(rows)} 行")
        if not rows:
            problems.append("原告信息表是空的")
    else:
        problems.append("缺少原告信息表 branch_info.json")
    hist = os.path.join(tmp, "branch_history")
    print(f"历史版本：{len(os.listdir(hist)) if os.path.isdir(hist) else 0} 份")
    auth = os.path.join(tmp, "auth.sqlite")
    if os.path.isfile(auth):
        con = sqlite3.connect(auth)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        con.close()
        print(f"账号库：完整性 {ok}，{users} 个账号")
        if ok != "ok":
            problems.append("账号库完整性检查未通过")
    else:
        problems.append("缺少账号库 auth.sqlite")
    if problems:
        sys.exit("恢复演练未通过：" + "；".join(problems))
    print("恢复演练通过（只在临时目录检查，未改动线上数据，临时文件已删除）")
'

verify() {
  python3 -I -c "$VERIFY_PY" "$1"
}

backup() {
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  local name
  name="legal-doc-$(date +%Y%m%d-%H%M%S).tar.gz"
  # 全局变量：EXIT 时函数已返回，trap 里引用局部变量会在 set -u 下报错
  PARTIAL="$BACKUP_DIR/.partial-$name"
  trap 'rm -f "${PARTIAL:-}"' EXIT
  docker compose exec -T backend python -c "$PACK_PY" > "$PARTIAL"
  # 写完先校验再改名：半截或损坏的备份不会冒充成功
  verify "$PARTIAL" > /dev/null
  mv "$PARTIAL" "$BACKUP_DIR/$name"
  trap - EXIT
  echo "备份完成：$BACKUP_DIR/$name（$(du -h "$BACKUP_DIR/$name" | cut -f1)）"
  local removed
  removed="$(find "$BACKUP_DIR" -maxdepth 1 -name 'legal-doc-*.tar.gz' -mtime +"$RETENTION_DAYS" -print -delete | wc -l)"
  echo "已删除超过 ${RETENTION_DAYS} 天的旧备份 ${removed} 份"
}

case "${1:-backup}" in
  backup) backup ;;
  verify)
    [ -n "${2:-}" ] || { echo "用法：deploy/backup.sh verify <备份文件>" >&2; exit 1; }
    verify "$2"
    ;;
  *) echo "用法：deploy/backup.sh [backup | verify <备份文件>]" >&2; exit 1 ;;
esac
