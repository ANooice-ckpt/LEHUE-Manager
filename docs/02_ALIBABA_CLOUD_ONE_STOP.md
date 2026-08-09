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
git clone https://github.com/YOUR_ACCOUNT/LightTrace.git
cd LightTrace
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
ENABLE_DOCS=true
```

生成 token：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

## H. 启动容器

```bash
docker compose up -d --build
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
cd /opt/LightTrace
git pull
docker compose up -d --build
```

`server/data/` 通过 bind mount 存在容器外，因此重新构建容器不会删除数据库和 raw archive。

## M. 最低限度备份

测试阶段至少每天把 `/opt/LightTrace/server/data` 打包一次并复制到另一台设备。正式阶段再接 OSS/自动快照，不要把唯一副本留在 ECS/轻量服务器本地盘。
