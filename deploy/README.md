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

必填 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`；可选 `DASHSCOPE_API_KEY_BACKUP`（付费备用 key：主 key 免费额度用完、被拒 401 / 403 时自动改用，日志会打印「主 key 被拒，改用付费备用 key」）；建议设置 `ADMIN_TOKEN`（长随机串，只有管理员知道，用于在「原告信息表」页面上传/回退表格；只想让对接律师上传，就只把口令告诉这位律师。每次上传 / 恢复会记下操作人的登录账号，在历史版本里可见）：

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
| `run_log.jsonl`、`run_log-YYYY-MM-DD.jsonl` | 运行日志：只记字段名、耗时、token 数、登录账号名，不含案件内容；按天切分 |
| `auth.sqlite` | 登录账号与会话（密码只存哈希），不随案件过期清除 |

- 案件会话与扫描件超过 `CASE_RETENTION_DAYS`（默认 7 天）自动清除，反馈随会话一并清除。
- 运行日志按天切分（UTC 日期），切分出的旧日志保留 `RUN_LOG_RETENTION_DAYS`（默认 90 天）后自动删除。它不含案件内容，用于统计使用量与律师改动率，所以比案件数据保留得久；案件清除后日志里的会话 ID 就对不上任何案件。
- 容器自身的 stdout 日志（`docker compose logs`）每个服务最多保留 5 个 10MB 文件。
- 备份与恢复：见下方「6.2 备份与恢复」。

- 查看律师在审核页提交的反馈（只读）：

```bash
docker compose exec backend python scripts/list_feedback.py            # 全部
docker compose exec backend python scripts/list_feedback.py --run <会话ID>
```

- 查看今天的运行日志：`docker compose exec backend tail -n 50 data/run_log.jsonl`（之前的在 `data/run_log-日期.jsonl`）
- 按账号统计使用情况（哪位律师哪天分析了几个案件、生成几份文书、用了多少 token / OCR 页；不含案件内容）：

```bash
docker compose exec backend python scripts/usage_report.py            # 最近 30 天
docker compose exec backend python scripts/usage_report.py --days 7   # 最近 7 天；--days 0 看全部
```

  本功能上线前的旧记录没有账号名，归在「（未登录 / 旧记录）」下。统计脚本会读当前日志和全部切分文件。
- 成本、耗时与错误汇总（每案成本、成本最高的节点、各模型用量、错误类别次数、律师在断点停留时长中位数与放弃率、付费备用 key 切换次数；不含案件内容）：

```bash
docker compose exec backend python scripts/cost_report.py --days 7
```

  金额按 `backend/.env` 的 `COST_PRICES` 换算（格式见 `.env.example`），单价以 DeepSeek / 阿里云百炼控制台价格页为准，并把取值日期写进 `COST_PRICES_DATE`；没配单价时只列 token 与页数，金额显示「未配置单价」。

### 6.1 案件 eval（开发用）

用律师已写好诉状的真实案卷跑系统，按标准字段逐项打分，看改规则 / 换模型后哪些案件变好、哪些变坏。真实案卷含当事人信息，**只放服务器私有目录，不进仓库**。脚本与 `expected.json` 写法见 `backend/scripts/eval_cases.py` 文件头。

一次性准备（容器里的 app 用户 uid 为 10001，目录须归它所有）：

```bash
sudo mkdir -p /srv/eval-cases
sudo chown 10001:10001 /srv/eval-cases && sudo chmod 700 /srv/eval-cases
```

放入案件（每个案件一个目录：`materials/` 放上传材料，`expected.json` 写标准字段，`cache/` 是录制的 OCR / LLM 结果，可一并带上）：

```bash
# 本机：scp eval-cases.tar.gz <用户>@<服务器>:/tmp/
sudo tar -xzf /tmp/eval-cases.tar.gz -C /srv/eval-cases
sudo chown -R 10001:10001 /srv/eval-cases && rm /tmp/eval-cases.tar.gz
```

运行（在仓库目录；用当前部署的代码，`--rm` 跑完即删临时容器）：

```bash
EVAL="docker compose run --rm -v /srv/eval-cases:/srv/eval-cases backend python scripts/eval_cases.py /srv/eval-cases"
$EVAL --strict          # 改规则后：只用缓存，不调用 OCR / LLM，几秒出结果、零费用
$EVAL                   # 缓存没有的才调用接口（新加案件时）
$EVAL --mode live       # 换模型、改提示词后：全部重新调用，重建缓存（每案约 3–4 分钟）
$EVAL --case 安装案2     # 只跑一个案件
sudo sh -c 'cat "$(ls -d /srv/eval-cases/_runs/*/ | tail -1)report.md"'   # 看最新报告
```

- 报告里有真实案件的取值，文件 0600；发给开发时只在私聊里发，不贴到 GitHub。
- eval 用临时目录存会话与扫描件，不写线上 `data/`，也不记运行日志；原告信息表读线上那份。

---

### 6.2 备份与恢复

**备份什么**：`deploy/backup.sh` 备份原告信息表（当前版 + 全部历史版本）和账号库（只带账号与密码哈希，不带登录会话，恢复后要重新登录）。

**不备份什么**：
- 案件会话和上传的扫描件（`checkpoints.sqlite`、`uploads/`）。按约定 7 天后清除，备份会让它们留得更久；丢了只是律师重新分析一次。
- 运行日志。不含案件内容，丢了不影响使用。

**每天自动备份**（在服务器上执行一次 `crontab -e`，加一行；路径按实际仓库位置改）：

```cron
17 3 * * * cd $HOME/legal-doc-gen && deploy/backup.sh >> $HOME/legal-doc-backup.log 2>&1
```

- 备份存到 `/srv/legal-doc-backups`（`BACKUP_DIR` 可改）：目录 0700、文件 0600，只有执行备份的用户能读；第一次需要 `sudo mkdir -p /srv/legal-doc-backups && sudo chown $USER /srv/legal-doc-backups`。
- 默认保留 30 天（`BACKUP_RETENTION_DAYS`），每次备份时自动删除更早的。
- 每份备份写完先自动做一遍恢复检查，通过了才保存；失败时脚本报错退出，不留半截文件，日志里能看到。
- 备份在同一台服务器上，服务器整个坏了就一起没了。建议另外开云盘快照（见下），或定期把最新一份拷到别处（拷贝的位置同样要限制访问）。

**恢复演练**（不碰线上数据，随时可以做）：

```bash
deploy/backup.sh verify /srv/legal-doc-backups/legal-doc-<日期>.tar.gz
```

解压到临时目录逐项检查后删除，只打印数量，例如「原告信息表：可读，N 行」「历史版本：N 份」「账号库：完整性 ok，N 个账号」「恢复演练通过」。

**真的需要恢复时**（会覆盖线上的原告信息表 / 账号，先确认要恢复哪一份）：

```bash
mkdir -m 700 /tmp/restore && tar -xzf /srv/legal-doc-backups/legal-doc-<日期>.tar.gz -C /tmp/restore
docker compose cp /tmp/restore/branch_info.json backend:/srv/backend/data/branch_info.json
docker compose cp /tmp/restore/branch_history backend:/srv/backend/data/       # 需要历史版本时
docker compose stop backend                                                     # 换账号库前先停后端
docker compose cp /tmp/restore/auth.sqlite backend:/srv/backend/data/auth.sqlite
docker compose start backend
docker compose exec -u root backend chown -R app:app /srv/backend/data         # cp 进去的文件属主改回 app
rm -rf /tmp/restore
```

原告信息表也可以不动服务器：在「原告信息表」页面用管理员口令「恢复历史版本」，或者上传 Excel。

**云盘快照（需要在云控制台核对）**：
- 服务器的云盘快照会连案件数据一起拍下。如果开了快照，保留期请设成不超过 7 天，和案件保留期一致；否则快照里的案件会留得比承诺的久。
- 核对结果和演练记录写在下面，只写非敏感信息，不写密码、下载链接、案件内容。

| 项目 | 结果 |
|---|---|
| 云盘快照是否开启 / 频率 / 保留期 | （待核对） |
| `deploy/backup.sh` 定时任务是否已加 | （待核对） |
| 最近一次恢复演练：日期 / 结果 | （待填写） |

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
- 部署先构建新版本（期间服务照常可用），再暂停接收新的分析（这时点「开始分析」会提示「系统正在更新，请一两分钟后再试」）、等进行中的分析结束（最多 10 分钟，`DEPLOY_DRAIN_TIMEOUT` 秒可调）才重启后端，律师只会碰到重启那几秒不可用。等满仍没结束的分析会中断（律师重新点「开始分析」即可），所以仍建议挑律师不用的时间批准。
- API 密钥只在服务器的 `backend/.env`，不进 GitHub；部署流程也不读它。
- 也可以在服务器上手动部署：`deploy/deploy.sh`（部署 origin/master 最新版），效果一样。

### 一次性配置

**① 在服务器上生成专用部署密钥**，并限定它只能运行部署脚本（拿不到 shell）：

```bash
ssh-keygen -t ed25519 -N "" -C github-deploy -f ~/.ssh/github_deploy
echo "command=\"$HOME/legal-doc-gen/deploy/deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/.ssh/github_deploy.pub)" >> ~/.ssh/authorized_keys
base64 -w0 ~/.ssh/github_deploy; echo       # 私钥（base64 单行，网页终端里复制不易出错）：下一步粘贴到 GitHub，之后删掉
echo "$(curl -s ifconfig.me) $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"   # 服务器指纹
```

**② 在 GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret** 添加 4 项：

| 名称 | 值 |
|---|---|
| `DEPLOY_HOST` | 服务器公网 IP |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | ① 中 `base64 -w0` 输出的那一整行（也接受原样多行私钥，含首尾 `-----BEGIN/END …-----` 行） |
| `DEPLOY_KNOWN_HOSTS` | ① 最后一行输出（`IP ssh-ed25519 AAAA…`） |

粘贴完回服务器删除私钥文件：`rm ~/.ssh/github_deploy`（公钥已写进 authorized_keys，不受影响）。

**③ 在 GitHub 仓库 → Settings → Environments → New environment**，名称填 `production`：
勾选 **Required reviewers** 并添加自己；**Deployment branches** 选 `Selected branches` 只允许 `master`。

**④ 验证**：仓库 Actions → Deploy → Run workflow，批准后应看到「部署成功」。

> 部署日志出现 `Load key … error in libcrypto` 或「DEPLOY_SSH_KEY 无法识别为私钥」：私钥粘贴不完整。
> 私钥文件已删的话，先删掉旧授权 `sed -i '/github-deploy/d' ~/.ssh/authorized_keys`，再从 ① 重新生成一遍，
> 把新的 base64 单行更新到 `DEPLOY_SSH_KEY`，然后在 Actions 里重新运行 Deploy。
>
> 停用自动部署：删掉服务器 `~/.ssh/authorized_keys` 里 `github-deploy` 那一行即可（GitHub 再也登录不上）。
