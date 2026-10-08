# 部署指南（UAT / 生产）

一台 Linux 云服务器 + Docker 即可跑起整套系统：

```
浏览器 ──HTTPS + 账号密码──▶ web（nginx：页面 + /api 反代 + Basic 认证）──▶ backend（FastAPI，不对外）
                                                                          └─ 命名卷 case-data（案件数据）
```

> **合规提醒**：真实客户案卷只能部署在**中国大陆**服务器上，且公网访问必须走 HTTPS。
> 大陆服务器用域名对外提供访问须先完成 **ICP 备案**；备案下来之前可用 IP + HTTP 在内网/本机验证，但不要上传真实案卷。

---

## 1. 服务器准备

- 规格：2 核 4G 起步（OCR 与 LLM 都走云端 API，本机不吃算力），系统盘 40G。
- 安全组只放行 **80 / 443**（以及你自己的 SSH 端口）；后端 8000 端口不对外。
- 安装 Docker（含 compose 插件）。阿里云 / 腾讯云镜像市场的 Docker 镜像，或：

```bash
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
# 再按 https://developer.aliyun.com/mirror/docker-ce 的说明添加源并 apt install docker-ce docker-compose-plugin
```

### 国内拉镜像 / 依赖慢

Docker Hub、PyPI、npm 在大陆经常超时，二选一：

1. 在 `/etc/docker/daemon.json` 配置 `registry-mirrors`（阿里云「容器镜像服务 ACR → 镜像加速器」页面给出的专属地址），然后 `sudo systemctl restart docker`；
2. 或在根目录 `.env` 里把 `PYTHON_IMAGE` / `NODE_IMAGE` / `NGINX_IMAGE` 改成你在 ACR 同步好的镜像地址。

pip 与 npm 源在 `.env` 里打开 `PIP_INDEX_URL` / `NPM_REGISTRY` 两行即可（见第 3 步）。

---

## 2. 拉代码

```bash
git clone https://github.com/phootako630/legal-doc-gen.git
cd legal-doc-gen
```

---

## 3. 配置（三个文件，都不进仓库）

### 3.1 API 密钥：`backend/.env`

```bash
cp backend/.env.example backend/.env
chmod 600 backend/.env
vi backend/.env
```

必填 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`；建议设置 `ADMIN_TOKEN`（长随机串，只有管理员知道，用于在「原告信息表」页面上传/回退表格）：

```bash
openssl rand -base64 24    # 生成一个口令
```

密钥只写在服务器这个文件里，不要发在聊天、邮件或提交到 Git。

### 3.2 部署参数：根目录 `.env`

```bash
cp deploy/compose.env.example .env
```

- `NGINX_MODE`：`http`（内网/验证）或 `https`（公网，必须）
- 国内服务器把 `PIP_INDEX_URL`、`NPM_REGISTRY` 两行取消注释

### 3.3 访问账号：`deploy/htpasswd`

系统本身没有登录功能，由 nginx 的账号密码挡在最前面。**启动前必须至少建一个账号**（否则 docker 会把 `deploy/htpasswd` 建成空目录，web 起不来）：

```bash
deploy/add-user.sh lawyer01      # 交互输入密码（≥10 位，不回显）
deploy/add-user.sh dev01
```

每人一个账号，便于以后停用。删账号：编辑 `deploy/htpasswd` 删掉对应行。改完执行 `docker compose restart web`。

---

## 4. HTTPS 证书（`NGINX_MODE=https` 时）

备案完成、域名解析到服务器后：

1. 在阿里云「数字证书管理服务」（或腾讯云 SSL 证书）申请免费 DV 证书；
2. 下载 **Nginx** 格式，得到 `.pem` 与 `.key`；
3. 放到服务器：

```bash
mkdir -p deploy/certs
cp xxx.pem deploy/certs/fullchain.pem
cp xxx.key deploy/certs/privkey.pem
chmod 644 deploy/certs/*.pem
```

免费证书有效期较短，到期前替换这两个文件后 `docker compose restart web`。

---

## 5. 启动 / 更新 / 停止

```bash
docker compose up -d --build        # 首次启动（构建约几分钟）
docker compose ps                   # backend 显示 healthy 即正常
docker compose logs -f backend      # 看后端日志
```

更新到最新代码：

```bash
git pull
docker compose up -d --build
```

停止：`docker compose down`（**不要加 `-v`**，否则会删除案件数据卷）。

> 后端必须单进程（Dockerfile 里 `--workers 1`）：上传/分析进度和进行中的分析任务在进程内存里。
> 更新重启时正在跑的分析会中断，律师重新点「开始分析」即可；已完成的会话保存在数据卷里不受影响。

---

## 6. 数据与运维

案件数据都在命名卷 `case-data`（容器内 `/srv/backend/data`，目录 0700、文件 0600，属主为非 root 的 app 用户）：

| 内容 | 说明 |
|---|---|
| `checkpoints.sqlite` | agent 会话（字段、律师决定）与律师反馈 |
| `uploads/` | 上传的扫描件（按需 OCR 用） |
| `branch_info.json`、`branch_history/` | 原告信息表（管理员维护，**需要备份**） |
| `run_log.jsonl` | 运行日志：只记字段名、耗时、token 数，不含案件内容 |

- 案件会话与扫描件超过 `CASE_RETENTION_DAYS`（默认 7 天）自动清除，反馈随会话一并清除。
- 备份原告信息表：

```bash
docker compose cp backend:/srv/backend/data/branch_info.json ./branch_info.backup.json
```

- 查看律师在审核页提交的反馈（只读）：

```bash
docker compose exec backend python scripts/list_feedback.py            # 全部
docker compose exec backend python scripts/list_feedback.py --run <会话ID>
```

- 查看运行日志：`docker compose exec backend tail -n 50 data/run_log.jsonl`

---

## 7. 上线前自检

```bash
curl -I http://<域名或IP>/                    # 无账号 → 401（https 模式下为 301 跳转）
curl -I -u lawyer01 https://<域名>/           # 输入密码后 → 200
curl -u lawyer01 https://<域名>/api/branches  # 返回 JSON 即后端连通
```

浏览器打开站点 → 输入账号密码 → 上传一套**脱敏或自造**的测试材料走通全流程，确认无误再交给律师使用真实案卷。
