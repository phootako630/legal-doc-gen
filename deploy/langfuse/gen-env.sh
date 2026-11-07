#!/usr/bin/env bash
# 为自托管 Langfuse 生成 .env：官方 docker-compose.yml 里标 CHANGEME 的默认口令全部换成随机值，
# 并在启动时建好组织、项目、API 密钥和管理员账号（headless 初始化，免去网页上手动注册）。
# 用法：bash gen-env.sh > .env && chmod 600 .env   （已有 .env 时不要重跑，否则数据库口令对不上）
set -euo pipefail
r() { openssl rand -hex "${1:-16}"; }
pg=$(r); minio=$(r)
cat <<ENV
LANGFUSE_IMAGE_TAG=4
POSTGRES_PASSWORD=$pg
DATABASE_URL=postgresql://postgres:$pg@postgres:5432/postgres
SALT=$(r)
ENCRYPTION_KEY=$(r 32)
NEXTAUTH_SECRET=$(r)
CLICKHOUSE_PASSWORD=$(r)
REDIS_AUTH=$(r)
MINIO_ROOT_PASSWORD=$minio
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=$minio
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=$minio
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=$minio
LANGFUSE_INIT_ORG_ID=legal-doc-gen
LANGFUSE_INIT_ORG_NAME=legal-doc-gen
LANGFUSE_INIT_PROJECT_ID=legal-doc-gen-eval
LANGFUSE_INIT_PROJECT_NAME=eval
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-$(r 8)
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-$(r 16)
LANGFUSE_INIT_USER_EMAIL=admin@legal-doc-gen.local
LANGFUSE_INIT_USER_NAME=admin
LANGFUSE_INIT_USER_PASSWORD=$(r 12)
ENV
