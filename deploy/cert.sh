#!/usr/bin/env sh
# 申请 / 续期 Let's Encrypt 免费 HTTPS 证书（HTTP-01 验证），放到 deploy/certs/ 供 nginx 使用。
#
# 用法（在仓库根目录、系统已用 docker compose 启动后执行）：
#   deploy/cert.sh issue 域名 [更多域名...]   # 首次申请，如 deploy/cert.sh issue example.com www.example.com
#   deploy/cert.sh renew                     # 续期：离到期不足 30 天才真正续，平时什么都不做；配合 crontab 每周跑
#
# 前提：域名已解析到本服务器，防火墙放行 80 端口（验证走 80，见 deploy/nginx/acme.conf）。
# 证书 90 天有效；renew 成功后自动让 nginx 重新加载，无需重启。
# 测试流程时可加环境变量 CERTBOT_STAGING=1，用 Let's Encrypt 测试环境（证书浏览器不信任，但不占正式额度）。
set -eu

cd "$(dirname "$0")/.."
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:v2.11.0}"
CERT_NAME="legal-doc"   # 固定证书名，续期与复制都按它找，不依赖域名
STAGING=""
[ "${CERTBOT_STAGING:-0}" = "1" ] && STAGING="--staging"

usage() {
  echo "用法：$0 issue 域名 [更多域名...]  |  $0 renew" >&2
  exit 1
}

action="${1:-}"
[ -n "$action" ] || usage
shift

case "$action" in
  issue)
    [ $# -ge 1 ] || usage
    domains=""
    for d in "$@"; do
      if ! printf '%s' "$d" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'; then
        echo "域名格式不对：$d" >&2
        exit 1
      fi
      domains="$domains -d $d"
    done
    certbot_cmd="certbot certonly --webroot -w /var/www/certbot --cert-name $CERT_NAME $domains \
      --agree-tos --register-unsafely-without-email --non-interactive --keep-until-expiring $STAGING"
    ;;
  renew)
    certbot_cmd="certbot renew --non-interactive $STAGING"
    ;;
  *)
    usage
    ;;
esac

# 这几个目录可能已被 docker compose 以 root 身份建好（挂载点不存在时自动创建），故权限在容器里设
mkdir -p deploy/certbot/www deploy/letsencrypt deploy/certs

# 在 certbot 容器里申请 / 续期，再把证书复制成 nginx 认的两个文件名（-L 取符号链接指向的实际文件）。
# 私钥 600：只有 root（nginx 主进程以 root 启动时读取）能读。
docker run --rm \
  -v "$PWD/deploy/letsencrypt:/etc/letsencrypt" \
  -v "$PWD/deploy/certbot/www:/var/www/certbot" \
  -v "$PWD/deploy/certs:/out" \
  --entrypoint sh "$CERTBOT_IMAGE" -c "
    set -e
    $certbot_cmd
    live=/etc/letsencrypt/live/$CERT_NAME
    if [ ! -f \"\$live/fullchain.pem\" ]; then
      echo '还没有证书：请先运行 deploy/cert.sh issue 域名' >&2
      exit 1
    fi
    chmod 700 /etc/letsencrypt /out
    cp -L \"\$live/fullchain.pem\" /out/fullchain.pem
    cp -L \"\$live/privkey.pem\" /out/privkey.pem
    chmod 644 /out/fullchain.pem
    chmod 600 /out/privkey.pem
  "

# nginx 在跑就重新加载证书（http 模式下也无妨）
if docker compose ps --status running --services 2>/dev/null | grep -qx web; then
  docker compose exec -T web nginx -s reload
fi

echo "证书已就绪：deploy/certs/fullchain.pem、deploy/certs/privkey.pem"
openssl x509 -in deploy/certs/fullchain.pem -noout -subject -enddate 2>/dev/null || true
