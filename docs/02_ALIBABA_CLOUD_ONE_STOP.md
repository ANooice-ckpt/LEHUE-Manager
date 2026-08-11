# Alibaba Cloud 测试开服路线（香港 / Simple Application Server）

这份文档对应工程测试。正式收集真实被试敏感定位数据前，应另行冻结伦理、隐私、数据地域和备份方案。

## A. 购买测试服务器

建议：
- Product: Simple Application Server
- Region: China (Hong Kong)
- OS image: Ubuntu 24.04 LTS（如购买页提供）
- Architecture: x86_64
- 2 vCPU / 2 GB RAM 即可跑当前 GPS prototype
- 系统盘 40 GB 级即可；未来光照大文件不放这块盘

记下：公网 IPv4、root/管理员登录方式。

## B. 域名

可先购买最终域名，也可以先用一个便宜测试域名。假设根域名为 `example.com`，GPS使用：

`gps.example.com`

DNS添加：
- Type: A
- Hostname: gps
- Value: 服务器公网 IPv4
- TTL: 默认即可

## C. 防火墙

公网只需要：
- TCP 80：Caddy申请证书并HTTP→HTTPS
- TCP 443：OwnTracks/浏览器 HTTPS
- TCP 22：SSH。测试时可先开放；熟悉以后应尽量限制来源IP。

**不要把 8000/8085 开到公网。** FastAPI 只在 Docker 内部接受 Caddy 转发。

## D. 第一次 SSH 登录

Windows PowerShell：

```powershell
ssh root@你的服务器IP
```

第一次会询问 host key，确认你连接的是自己刚创建的服务器后输入 `yes`。

## E. 安装 Git 与 Docker

服务器上：

```bash
apt update
apt install -y git ca-certificates curl
```

Docker 请优先按 Docker 官方 Ubuntu 安装文档安装 Docker Engine + Compose plugin。安装完验证：

```bash
docker --version
docker compose version
```

## F. 从 GitHub 拉代码

```bash
cd /opt
git clone https://github.com/ANooice-ckpt/LEHUE-Manager.git
cd LEHUE-Manager
```

如果仓库是 private repo，请使用 GitHub 推荐的 SSH key / token 方法，不要把 GitHub 密码写到命令中。

## G. 配置生产 `.env`

```bash
cp .env.example .env
nano .env
```

至少修改：

```text
DOMAIN=gps.example.com
ADMIN_TOKEN=随机长token
CREDENTIAL_ENCRYPTION_KEY=随机Fernet密钥
ENABLE_DOCS=true
```

生成 token：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
python3 -c 'import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

## H. 启动容器

```bash
./scripts/server_start.sh test
docker compose ps
```

看日志：

```bash
docker compose logs --tail=200
```

当 DNS 已正确指向服务器、80/443 可访问时，Caddy 会自动申请 HTTPS 证书。

## I. 创建 TEST01

```bash
docker compose exec api python scripts/create_participant.py TEST01
```

保存输出的 password。

## J. 验证 HTTPS

浏览器：

`https://gps.example.com/health`

应该返回 `status=ok`。

## K. 配置 OwnTracks

- Mode: HTTP
- URL: `https://gps.example.com/api/v1/gps/owntracks`
- Username: TEST01
- Password: 上一步生成的 password
- Monitoring/定位模式：统一选择 **Move**
- Android：Move interval 设为 `10 s`
- iOS：`locatorInterval=10`、`locatorDisplacement=10`、`adapt=0`、`downgrade=0`

10 秒是目标采样设置，不是逐点验收条件。系统后台调度、定位环境、网络和离线补传会令实际间隔变化；Admin 会把近期收到但记录时间明显较早的数据标为“补传中”。这些推荐值也显示在 Admin 被试凭据窗口旁，方便 RA 现场核对。

手机产生位置以后，再访问：

```bash
curl https://gps.example.com/health
```

管理员状态：

```bash
curl -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  https://gps.example.com/api/v1/admin/gps/status/TEST01
```

## L. 更新代码

以后发现 bug：Windows 本地改 → 本地测试 → push GitHub，然后服务器：

```bash
cd /opt/LEHUE-Manager
git pull
docker compose up -d --build
```

`server/data/` 通过 bind mount 存在容器外，因此重新构建容器不会删除数据库和 raw archive。

## M. 最低限度备份

云端 TEST / PROD 的 ECS 应先绑定一个最小权限 RAM Role，并设置：

```dotenv
LIGHT_STORAGE_BACKEND=oss
OSS_CREDENTIAL_MODE=ecs_ram_role
OSS_ROLE_NAME=lehue-test-ecs
OSS_BUCKET=lehue-lighting-test
OSS_REGION=cn-hongkong
# ECS 做 QC 可用内网 endpoint；浏览器必须使用公网 endpoint。
OSS_ENDPOINT=https://oss-cn-hongkong-internal.aliyuncs.com
OSS_PUBLIC_ENDPOINT=https://oss-cn-hongkong.aliyuncs.com
```

不要在 PROD `.env` 配置长期 `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET`。RAM Role 至少需要 Lighting 前缀的 Put/Get/Head/Delete 权限，以及备份 bucket 前缀的 Put 权限；TEST 与 PROD 使用不同 role 和 bucket。

Participant Portal 使用限定 object key、短时有效的签名 PUT URL。Lighting bucket 的 CORS 只允许正式 Portal Origin、`PUT` 和 `x-oss-meta-sha256`，例如允许来源 `https://gps.example.com`；修改域名后同步更新 CORS。上传状态保存在 SQLite：`pending` 可继续上传、`uploaded` 可重新触发 QC、`qc` 表示 acquisition QC 已落库。

配置独立私有备份桶（不要使用公开读写权限）：

```dotenv
BACKUP_OSS_BUCKET=lehue-private-backup-test
BACKUP_OSS_PREFIX=lehue-backups
```

脚本会在线快照 `lehue.sqlite3`、`lehue_identity.sqlite3`，加入 GPS raw JSONL 后上传 OSS；Lighting raw 已是 canonical OSS object，不重复备份。先手工测试：

```bash
cd /opt/LEHUE-Manager
export LEHUE_ENV=test
docker compose exec -T api python scripts/backup_to_oss.py
```

确认 OSS 中出现对象并完成一次解压/SQLite 打开检查后，再加入 `crontab -e`（例如每天 03:20）：

```cron
20 3 * * * cd /opt/LEHUE-Manager && LEHUE_ENV=test bash scripts/server_backup.sh >> /var/log/lehue-backup.log 2>&1
```

PROD 将 `LEHUE_ENV` 改为 `prod`，并使用独立 bucket/RAM 权限。脚本对象键包含环境名，Lighting 不重复复制，失败会以非零状态退出供 cron 记录。


## N. v0.2.2 dependency policy

Docker image only installs `server/requirements.txt` (runtime dependencies). `pytest` and `httpx` used for local development/tests live in `server/requirements-dev.txt` and are not required in the production container.
