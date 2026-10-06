#!/bin/bash
# 解压律师发来的案卷压缩包（.rar / .zip / .7z）到一个新的空目录，中文文件名不乱码。
# 用法：extract.sh <压缩包> <输出目录（须不存在或为空，放在 scratchpad 下）>
set -euo pipefail
ARCHIVE="${1:?用法：extract.sh <压缩包> <输出目录>}"
OUT="${2:?用法：extract.sh <压缩包> <输出目录>}"
export LC_ALL=C.UTF-8 LANG=C.UTF-8   # 不设的话 unrar 会把中文名写成 ???? 并解压失败

if [ -d "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
  echo "输出目录非空：$OUT（案卷是不可信数据，每次解到新的空目录）" >&2
  exit 1
fi
mkdir -p "$OUT"

need() {  # 缺工具时按需安装（不放进 session-start，避免拖慢每次启动）
  command -v "$1" >/dev/null 2>&1 || apt-get install -y -q "$2" >/dev/null
}

case "${ARCHIVE,,}" in
  *.rar)
    # 用官方 unrar：unar 解 RAR5 会报 "Attempted to read more data"，系统 7z 不含 RAR 解码器
    need unrar unrar
    unrar x -o+ -inul "$ARCHIVE" "$OUT/"
    ;;
  *.zip)
    # Windows 打的 zip 常用 GBK 文件名，unar 能自动识别编码
    need unar unar
    unar -q -o "$OUT" "$ARCHIVE"
    ;;
  *.7z)
    need 7z p7zip-full
    7z x -y -o"$OUT" "$ARCHIVE" >/dev/null
    ;;
  *)
    echo "不支持的压缩格式：$ARCHIVE" >&2
    exit 1
    ;;
esac
find "$OUT" -type f -printf '%10s  %p\n'
