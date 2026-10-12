# 部署指南（UAT / 生产）

一台 Linux 云服务器 + Docker 即可跑起整套系统：

```
浏览器 ──HTTPS──▶ web（nginx：页面 + /api 反代）──▶ backend（FastAPI：登录校验 + 业务，不对外）
                                                                          └─ 命名卷 case-data（案件数据）
```

> **部署地点与合规**：当前部署在**中国香港**服务器（免 ICP 备案），律所已书面确认接受案卷存放在香港、扫描件经阿里云国际站（新加坡）做 OCR。
> 案卷含一般个人信息（联系人、法定代表人姓名等），不上传身份证、银行流水等敏感个人信息；公网访问必须走 HTTPS。
> 若日后委托方或律所要求数据留在大陆：换大陆服务器并完成 **ICP 备案**（备案前不要上传真实案卷），同时把 OCR 换成阿里云国内站（另需国内站账号与 API Key）。

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

必填 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`；建议设置 `ADMIN_TOKEN`（长随机串，只有管理员知道，用于在「原告信息表」页面上传/回退表格；只想让对接律师上传，就只把口令告诉这位律师。每次上传 / 恢复会记下操作人的登录账号，在历史版本里可见）：

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

访问账号在系统启动后创建，见第 5 步。

---

## 4. HTTPS 证书（`NGINX_MODE=https` 时）

域名解析到服务器后（大陆服务器还须先完成备案），用 `deploy/cert.sh` 申请 **Let's Encrypt 免费证书**（90 天有效，自动续期）。
证书申请要在系统**已经以 http 模式跑起来之后**做（验证文件经 80 端口提供），所以首次上线的顺序是：

```bash
# ① 先按第 5 步以 NGINX_MODE=http 启动，确认 http://域名 能打开
# ② 申请证书（域名换成你的；带 www 的也一起写上，前提是 www 也解析到了本服务器）
deploy/cert.sh issue example.com www.example.com
# ③ 切到 https 模式并重建 web 容器
sed -i 's/^NGINX_MODE=.*/NGINX_MODE=https/' .env
docker compose up -d
# ④ 每周一凌晨自动检查续期（离到期不足 30 天才真正续，续完自动让 nginx 重新加载）
(crontab -l 2>/dev/null; echo "17 3 * * 1 cd $PWD && deploy/cert.sh renew >> $HOME/cert-renew.log 2>&1") | crontab -
```

- 证书在 `deploy/certs/`（私钥 600），Let's Encrypt 账号与原始证书在 `deploy/letsencrypt/`，都不进 git；**换服务器时把这两个目录一起拷走**，或在新服务器重新 `issue`。
- 申请失败最常见的原因：域名还没解析到本机（`ping 域名` 看 IP）、防火墙没放行 80 端口。
- 想先演练不占正式额度：命令前加 `CERTBOT_STAGING=1`（测试证书浏览器不信任，演练完去掉再正式申请一次）。
- 也可以改用云厂商（阿里云 / 腾讯云）免费证书：下载 **Nginx** 格式，把证书和私钥分别放成 `deploy/certs/fullchain.pem`、`deploy/certs/privkey.pem`（私钥 `chmod 600`），然后 `docker compose restart web`；这类证书要自己记得到期前更换。

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

### 5.1 登录账号

打开网站先显示中文登录页，账号密码正确才能进入；登录后 7 天内免登录，页头有「修改密码」和「退出登录」。
系统没有注册入口，账号由管理员在服务器上建（每人一个，便于停用）：

```bash
docker compose exec backend python scripts/manage_users.py add lawyer01   # 交互输入密码（≥10 位，不回显）
docker compose exec backend python scripts/manage_users.py add dev01
docker compose exec backend python scripts/manage_users.py list            # 列出账号
docker compose exec backend python scripts/manage_users.py passwd lawyer01 # 律师忘记密码时重设
docker compose exec backend python scripts/manage_users.py disable lawyer01  # 停用（立即退出所有电脑）
```

- 律师登录后可在页头「修改密码」自己改；改完其他电脑上的登录会失效。
- 同一 IP 对同一账号连续输错 5 次（或同一 IP 累计输错 20 次）锁定 15 分钟；后端重启后计数清零。
- 账号库在数据卷里（`auth.sqlite`），只存加盐哈希，不存明文密码；不随案件过期清除。

> **从旧版（nginx 弹窗账号）升级**：`git pull && docker compose up -d --build` 后旧的 `deploy/htpasswd` 不再使用，
> 用上面的命令把账号重建一遍即可；`deploy/htpasswd` 文件可删除。

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
| `auth.sqlite` | 登录账号与会话（密码只存哈希），不随案件过期清除 |

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
curl -s -o /dev/null -w "%{http_code}\n" https://<域名>/api/branches  # 未登录 → 401
curl -s https://<域名>/api/auth/me     # {"auth_enabled":true,...} 即已开启登录
```

浏览器打开站点 → 登录页输入账号密码 → 上传一套**脱敏或自造**的测试材料走通全流程，确认无误再交给律师使用真实案卷。

---

## 8. 自动部署（GitHub Actions + 审批）

合并 PR 到 `master` 且 CI 通过后，GitHub 先等你**批准**，批准后才登录服务器执行 `deploy/deploy.sh`：
拉取该提交 → 重新构建启动 → 检查服务 → **失败自动回滚到上一个版本**。部署记录和日志在仓库 Actions 页面的「Deploy」里。

- 只部署 `master` 上的提交；服务器上有人手改过受版本控制的文件时拒绝部署（不覆盖手工修改）。
- 部署会重启后端，进行中的分析会中断（律师重新点「开始分析」即可），建议挑律师不用的时间批准。
- API 密钥只在服务器的 `backend/.env`，不进 GitHub；部署流程也不读它。
- 也可以在服务器上手动部署：`deploy/deploy.sh`（部署 origin/master 最新版），效果一样。

### 一次性配置

**① 在服务器上生成专用部署密钥**，并限定它只能运行部署脚本（拿不到 shell）：

```bash
ssh-keygen -t ed25519 -N "" -C github-deploy -f ~/.ssh/github_deploy
echo "command=\"$HOME/legal-doc-gen/deploy/deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/.ssh/github_deploy.pub)" >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy                     # 私钥：下一步粘贴到 GitHub，之后删掉
echo "$(curl -s ifconfig.me) $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"   # 服务器指纹
```

**② 在 GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret** 添加 4 项：

| 名称 | 值 |
|---|---|
| `DEPLOY_HOST` | 服务器公网 IP |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | ① 中 `cat ~/.ssh/github_deploy` 输出的全部内容（含首尾 `-----BEGIN/END …-----` 行） |
| `DEPLOY_KNOWN_HOSTS` | ① 最后一行输出（`IP ssh-ed25519 AAAA…`） |

粘贴完回服务器删除私钥文件：`rm ~/.ssh/github_deploy`（公钥已写进 authorized_keys，不受影响）。

**③ 在 GitHub 仓库 → Settings → Environments → New environment**，名称填 `production`：
勾选 **Required reviewers** 并添加自己；**Deployment branches** 选 `Selected branches` 只允许 `master`。

**④ 验证**：仓库 Actions → Deploy → Run workflow，批准后应看到「部署成功」。

> 停用自动部署：删掉服务器 `~/.ssh/authorized_keys` 里 `github-deploy` 那一行即可（GitHub 再也登录不上）。
