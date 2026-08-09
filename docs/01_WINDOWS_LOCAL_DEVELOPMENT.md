# Windows 本地开发（不使用 Docker）

这仍然是推荐的日常开发方式。Docker 只用于最终复现和部署。

## 1. 打开 PowerShell

```powershell
cd D:\LightTrace\server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 2. 在仓库根目录创建 `.env`

复制 `.env.example` 为 `.env`。注意：直接本地运行 FastAPI 时，Python 不会自动读取 `.env`；最简单的测试方式是在当前 PowerShell 设置环境变量：

```powershell
$env:ADMIN_TOKEN="换成一串长随机字符"
$env:DATA_DIR="./data"
$env:DB_PATH="./data/lighttrace.sqlite3"
$env:RAW_ARCHIVE_DIR="./data/raw/gps"
```

随机 token：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 3. 创建 TEST01

```powershell
python scripts\create_participant.py TEST01
```

复制终端打印的 password。

## 4. 启动

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8085 --reload
```

浏览器：`http://127.0.0.1:8085/health`

## 5. OwnTracks 配置

- Mode: HTTP
- URL: `https://你的-Cloudflare-临时域名/api/v1/gps/owntracks`
- Username: `TEST01`
- Password: 第3步得到的密码

Cloudflare 继续映射本机 `http://localhost:8085` 即可。

## 6. 管理接口

PowerShell：

```powershell
$headers=@{Authorization="Bearer $env:ADMIN_TOKEN"}
Invoke-RestMethod -Headers $headers http://127.0.0.1:8085/api/v1/admin/gps/status/TEST01
```

CSV：

```powershell
Invoke-WebRequest -Headers $headers http://127.0.0.1:8085/api/v1/admin/gps/export/TEST01.csv -OutFile TEST01_gps.csv
```
