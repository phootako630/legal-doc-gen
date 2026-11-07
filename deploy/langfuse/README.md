# 自托管 Langfuse（开发 / eval 专用）

Langfuse 是 LLM 应用的链路追踪与评测平台。本项目只在 **eval** 里用它（`scripts/eval_cases.py --langfuse`）：每个案件一条 trace，里面是这个案件的每次 LLM 抽取、OCR、LLM 裁判调用（提示词、返回、是否命中缓存），逐字段结论和准确率写成 score，可以在网页上按案件、按字段翻看和对比。**线上系统不接 Langfuse。**

## 数据边界

- trace 里有案卷内容（提示词里的合同文字、抽取结果、标准值）：**只发到自托管的实例**。`--langfuse` 遇到 `*.langfuse.com`（Langfuse Cloud）默认拒绝；`--langfuse-allow-cloud` 只用于虚构案卷集 `eval_fixtures/`。
- OCR 只记页面图片的哈希，不上传图片。
- 网页不对公网开放：`compose.override.yml` 把所有端口绑到 `127.0.0.1`，从本机或 SSH 隧道访问。

## 放在哪台机器

**开发者自己的电脑**（Docker Desktop，内存建议 16 GB 以上）。Langfuse v4 一整套是 7 个容器（网页、worker、AI 网关、PostgreSQL、ClickHouse、Redis、MinIO），实测空载约占 2.4 GB 内存，ClickHouse 随数据增长。

- **不要装在线上那台 2 核 4 GB 的服务器上**：会和律师在用的系统抢内存。
- 真实案卷也可以记到这台电脑上的 Langfuse（做法见下文「真实案卷」）。案卷文件和 trace 都会留在这台电脑上：电脑要开磁盘加密、不外借，不用了就删掉（见「清理」）。

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

首次启动若提示 `dependency failed to start: container ...-minio-1 is unhealthy`，是 MinIO 起得慢，再执行一次 `docker compose up -d` 即可。

网页：`http://127.0.0.1:3000`，账号密码是 `.env` 里的 `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`。在别的电脑上看：`ssh -L 3000:127.0.0.1:3000 <Langfuse 所在机器>`，再打开本机的 `http://127.0.0.1:3000`。

停止：`docker compose down`（数据在 Docker 卷里，保留）。

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

## 真实案卷

真实案卷在服务器的 `/srv/eval-cases`，带着录好的缓存（`cache/`）。拿到电脑上**严格回放**：不调用 OCR / LLM、不需要 API 密钥、零费用，结果与服务器上一致，同时把每个案件记到电脑上的 Langfuse。

1. 服务器上打包案卷和原告信息表（在仓库目录执行；原告信息表在后端容器的数据卷里，真实案卷的 eval 要用它）：

   ```bash
   sudo tar -czf /tmp/eval-cases.tar.gz -C /srv/eval-cases --exclude=_runs .
   docker compose cp backend:/srv/backend/data/branch_info.json /tmp/branch_info.json
   sudo chown "$USER" /tmp/eval-cases.tar.gz /tmp/branch_info.json
   ```

2. 电脑上取回、解开，原告信息表放进案卷根目录（eval 发现根目录有 `branch_info.json` 就用它），然后删掉服务器上的临时文件：

   ```bash
   mkdir -p ~/eval-cases ~/eval-runs
   scp <服务器>:/tmp/eval-cases.tar.gz <服务器>:/tmp/branch_info.json ~/
   tar -xzf ~/eval-cases.tar.gz -C ~/eval-cases && mv ~/branch_info.json ~/eval-cases/ && rm ~/eval-cases.tar.gz
   ssh <服务器> rm /tmp/eval-cases.tar.gz /tmp/branch_info.json
   ```

3. 用仓库的后端镜像跑，接到 Langfuse 的 Docker 网络上（Langfuse 在 `~/langfuse` 启动时网络名是 `langfuse_default`，用 `docker network ls` 确认），这样不用在电脑上装 Python：

   ```bash
   cd <仓库> && docker build -f backend/Dockerfile -t legal-doc-gen-eval .
   docker run --rm --network langfuse_default \
     -v ~/eval-cases:/srv/eval-cases:ro -v ~/eval-runs:/srv/eval-runs \
     -e LANGFUSE_HOST=http://langfuse-web:3000 \
     -e LANGFUSE_PUBLIC_KEY=<pk-lf-…> -e LANGFUSE_SECRET_KEY=<sk-lf-…> \
     legal-doc-gen-eval python scripts/eval_cases.py /srv/eval-cases --strict --langfuse --out /srv/eval-runs
   ```

   容器里以 uid 10001 运行：Mac / Windows 的 Docker Desktop 不用管；Linux 电脑上先 `chmod 777 ~/eval-runs`，否则写不了报告。

   结束时打印的 `Langfuse：http://langfuse-web:3000` 是容器里的地址，网页照常在 `http://127.0.0.1:3000` 打开。报告在 `~/eval-runs/<运行时间>/report.md`。

   严格回放提示缓存未命中，说明代码改动让系统发了新的提示词或读了新的页：回服务器上按 `deploy/README.md`「案件 eval」重录，再重新打包。

## 清理

```bash
cd ~/langfuse && docker compose down -v     # 连同 trace 数据一起删
rm -rf ~/eval-cases ~/eval-runs             # 案卷与报告
```

