#!/usr/bin/env sh
# 添加 / 修改访问账号（nginx Basic 认证），写入 deploy/htpasswd。用法：deploy/add-user.sh 用户名
# 密码交互输入、不回显，也不会出现在 shell 历史里。改完执行 docker compose restart web 生效。
set -eu

user="${1:-}"
if ! printf '%s' "$user" | grep -Eq '^[A-Za-z0-9_.-]{2,32}$'; then
  echo "用法：$0 用户名（2–32 位字母、数字、_ . -）" >&2
  exit 1
fi
command -v openssl >/dev/null 2>&1 || { echo "需要 openssl" >&2; exit 1; }

file="$(dirname "$0")/htpasswd"

printf '设置「%s」的密码（至少 10 位）：' "$user"
stty -echo; read -r pass; stty echo; echo
printf '再输入一次：'
stty -echo; read -r pass2; stty echo; echo
[ "$pass" = "$pass2" ] || { echo "两次输入不一致" >&2; exit 1; }
[ "${#pass}" -ge 10 ] || { echo "密码至少 10 位" >&2; exit 1; }

hash="$(printf '%s' "$pass" | openssl passwd -6 -stdin)"
touch "$file"
# 先删掉同名旧记录再追加：按冒号前的用户名精确比较（用户名可含「.」，不能当正则用）
tmp="$(mktemp)"
awk -F: -v user="$user" '$1 != user' "$file" > "$tmp"
printf '%s:%s\n' "$user" "$hash" >> "$tmp"
mv "$tmp" "$file"
# nginx 容器内的 worker 用户需要可读
chmod 644 "$file"
echo "已保存账号「$user」。执行 docker compose restart web 生效。"
