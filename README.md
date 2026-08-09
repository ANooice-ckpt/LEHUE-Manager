# LEHUE-Manager v0.2.2

**LEHUE = Light Exposure Histories in Urban Environments**

这是 LEHUE 的统一管理与数据基础设施仓库。当前阶段只真正实现 **GPS acquisition**；Light、Questionnaire、Cross-modal QC、Admin integration 保留模块位置，后续逐步接入。

## 当前已实现的数据链

OwnTracks → HTTP/HTTPS → FastAPI → participant authentication → SQLite + append-only JSONL mirror → acquisition QC / CSV export

## 仓库结构

```text
LEHUE-Manager/
├── server/
│   ├── app/
│   │   ├── core/
│   │   └── modules/
│   │       ├── gps/             # v0.2.x 已实现
│   │       ├── light/           # reserved
│   │       ├── questionnaire/   # reserved
│   │       ├── qc/              # reserved
│   │       └── admin/           # reserved
│   ├── scripts/
│   ├── tests/
│   └── data/                    # 不提交 Git
├── scripts/                     # Windows 开发快捷脚本
├── docs/
├── docker-compose.yml
└── Caddyfile
```

## v0.2.2 变化

- 修复 Windows setup 会运行 `pytest`、但未安装 `pytest` 的依赖缺口。
- 将服务器运行依赖与本地开发/测试依赖拆分为 `requirements.txt` 与 `requirements-dev.txt`。
- Windows setup 默认使用 `--no-cache-dir`，避免 pip 安装缓存继续占用系统盘。
- setup 对每个原生命令检查退出码，依赖安装失败会立即停止，不再留下看似“初始化成功”的半成品环境。
- setup 安装后主动验证 `fastapi`、`uvicorn`、`httpx`、`pytest`。
- start 启动前检查 `fastapi/uvicorn`，依赖缺失时给出明确修复命令。
- 新增 `scripts/windows_doctor.ps1`，一键检查 venv、关键模块及 C:/项目盘剩余空间。
- 应用版本号改由代码单一来源管理，不再放进 `.env`，避免覆盖升级后仍错误显示旧版本。
- 保持 v0.2.1 的 LEHUE 命名、北京时间日级 QC、GPS 数据模型和 API 不变。

## 第一次本地运行

在仓库根目录 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
.\scripts\windows_start.ps1
```

浏览器打开：

- `http://127.0.0.1:8085/health`
- `http://127.0.0.1:8085/docs`

详细流程见 `docs/01_WINDOWS_LOCAL_DEVELOPMENT.md`。

## 安全边界

- `.env`、participant secrets、SQLite、raw JSONL、真实 GPS 均不得提交 Git。
- `/health` 不返回坐标和 participant ID。
- 正式公网部署时 FastAPI 8000 不直接开放，只允许 Caddy 暴露 80/443。
- 当前仍是工程 prototype；正式被试前还需冻结隐私、备份、数据地域、恢复与审计方案。
