# Windows 本地开发 — LEHUE v0.2.2

日常开发继续使用 Windows + VS Code。Docker 只用于可重复部署，不要求你在容器里写代码。

## 0. 目录

仓库示例：

```text
D:\PhD\LEHUE\LEHUE-Manager
```

虚拟环境会创建在：

```text
D:\PhD\LEHUE\LEHUE-Manager\server\.venv
```

因此安装后的 Python 包主要位于项目所在盘。v0.2.2 setup 还会关闭 pip 下载缓存，避免安装过程中继续向 C: 的默认 pip cache 写入安装包缓存。

## 1. 第一次初始化

在仓库根目录 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
```

脚本会：

1. 创建/复用 `server\.venv`；
2. 安装 `requirements-dev.txt`（其中包含运行依赖与 pytest/httpx）；
3. 验证关键模块均可导入；
4. 首次生成 `.env` 和随机 `ADMIN_TOKEN`；
5. 运行自动化测试。

成功时应看到：

```text
LEHUE local setup is ready.
```

## 2. 环境体检

任何时候如果怀疑环境不完整：

```powershell
.\scripts\windows_doctor.ps1
```

它会检查：

- venv 是否存在；
- Python 实际路径；
- fastapi / uvicorn / httpx / pytest；
- 项目盘和 C: 剩余空间。

## 3. 启动本地服务器

```powershell
.\scripts\windows_start.ps1
```

浏览器：

- `http://127.0.0.1:8085/health`
- `http://127.0.0.1:8085/docs`

保持这个 PowerShell 窗口打开。Ctrl+C 停止服务器。

## 4. 创建测试 participant

另开一个仓库根目录 PowerShell：

```powershell
.\scripts\windows_create_test_participant.ps1 TEST01
```

password 会在终端显示，也会以加密副本保存在 TEST 数据库中；正式被试可从 Web Admin 的“查看凭据”复制。

## 5. 本地 smoke test

服务器正在运行时：

```powershell
.\scripts\windows_smoke_test.ps1 -Password "刚才的密码"
```

HEALTH / INGEST / STATUS 均返回 200，即代表最小 GPS 链路正常。

## 6. OwnTracks + Cloudflare 临时测试

保持 LEHUE 本地服务器运行，再把 Cloudflare tunnel 指向：

```text
http://localhost:8085
```

OwnTracks HTTP URL：

```text
https://<trycloudflare-host>/api/v1/gps/owntracks
```

Username = `TEST01`，Password = participant password。

## 7. 手工跑测试

```powershell
cd .\server
.\.venv\Scripts\python.exe -m pytest -q
```

## 8. 依赖说明

`server/requirements.txt`：云端运行所需，仅 FastAPI/Uvicorn 等运行依赖。

`server/requirements-dev.txt`：Windows 开发/测试使用，引用运行依赖并额外安装 pytest/httpx。

## Web Admin (v0.3+)

首次创建 PI 登录账号：

```powershell
.\scripts\windows_create_admin.ps1 -Username pi -Role pi -DisplayName "PI"
```

启动后访问 `http://127.0.0.1:8085/admin`。详见 `docs/03_WEB_ADMIN_V0_3.md`。
