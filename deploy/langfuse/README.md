# 自托管 Langfuse（开发 / eval 专用）

Langfuse 是 LLM 应用的链路追踪与评测平台。本项目只在 **eval** 里用它（`scripts/eval_cases.py --langfuse`）：每个案件一条 trace，里面是这个案件的每次 LLM 抽取、OCR、LLM 裁判调用（提示词、返回、是否命中缓存），逐字段结论和准确率写成 score，可以在网页上按案件、按字段翻看和对比。**线上系统不接 Langfuse。**

## 数据边界

- trace 里有案卷内容（提示词里的合同文字、抽取结果、标准值）：**只发到自托管的实例**。`--langfuse` 遇到 `*.langfuse.com`（Langfuse Cloud）默认拒绝；`--langfuse-allow-cloud` 只用于虚构案卷集 `eval_fixtures/`。
- OCR 只记页面图片的哈希，不上传图片。
- 网页不对公网开放：`compose.override.yml` 把所有端口绑到 `127.0.0.1`，从本机或 SSH 隧道访问。

## 放在哪台机器

Langfuse v4 一整套是 7 个容器（网页、worker、AI 网关、PostgreSQL、ClickHouse、Redis、MinIO）。实测空载就占约 2.4 GB 内存（网页 1.3 GB、worker 0.7 GB），ClickHouse 随数据增长；官方单机最低配置里 ClickHouse 一项就要 2 核 8 GB。

- **不要装在线上那台 2 核 4 GB 的服务器上**：会和律师在用的系统抢内存。
- 建议：开发者自己的电脑（Docker Desktop，内存 16 GB 以上），或单独一台 4 核 16 GB 的机器。真实案卷的 eval 要发 trace 时，Langfuse 所在机器须满足与案卷相同的存放要求（见 `deploy/README.md`「部署地点与合规」）。

## 启动

需要 Docker 与 Docker Compose 2.24 以上。

```bash
mkdir -p ~/langfuse && cd ~/langfuse
# 官方 compose 文件（镜像版本由 .env 的 LANGFUSE_IMAGE_TAG=4 固定在 v4）
curl -fsSLO https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml
cp <仓库>/deploy/langfuse/compose.override.yml .
# 生成随机口令 + 启动时自动建好项目、API 密钥和管理员账号；已有 .env 时不要重跑
bash <仓库>/deploy/langfuse/gen-env.sh > .env && chmod 600 .env
docker compose up -d
curl -s http://127.0.0.1:3000/api/public/health    # {"status":"OK",...} 即就绪（首次约 1–2 分钟）
```

网页：`http://127.0.0.1:3000`，账号密码是 `.env` 里的 `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`。在别的电脑上看：`ssh -L 3000:127.0.0.1:3000 <Langfuse 所在机器>`，再打开本机的 `http://127.0.0.1:3000`。

停止：`docker compose down`（数据在 Docker 卷里，保留）；连数据一起删：`docker compose down -v`。

## 跑 eval 时记 trace

```bash
cd backend
export LANGFUSE_HOST=http://127.0.0.1:3000
export LANGFUSE_PUBLIC_KEY=<.env 里的 LANGFUSE_INIT_PROJECT_PUBLIC_KEY>
export LANGFUSE_SECRET_KEY=<.env 里的 LANGFUSE_INIT_PROJECT_SECRET_KEY>
python scripts/eval_cases.py eval_fixtures --strict --langfuse
```

`LANGFUSE_HOST` 要从跑 eval 的地方能访问到（eval 在容器里跑时，`127.0.0.1` 指的是容器自己）。连不上或密钥不对会直接报错退出，不会跑完了才发现没记上。

在网页里看：

- **Tracing / Sessions**：一次 eval 运行是一个 session（名字是运行时间，如 `20261107-031813`），每个案件一条 trace（`eval <案件名>`）；trace 里逐条看 LLM / OCR / 裁判调用的提示词和返回，`metadata.cached` 标出是否回放自缓存。
- **Scores**：每个字段一条结论（正确 / 错误 / 缺失 / 待定，备注里是标准值与系统值），`裁判/<字段>` 是 LLM 裁判的结论，`准确率` 是案件准确率。
