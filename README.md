# LEHUE-Manager v0.3.0

**LEHUE = Light Exposure Histories in Urban Environments**

LEHUE-Manager 是实验运营与采集状态基础设施。**科研分析不搬云端。** 当前真正实现两条主线：

1. GPS acquisition：OwnTracks → HTTP/HTTPS → FastAPI → participant authentication → SQLite + append-only JSONL → acquisition QC / CSV export。
2. Web Admin：PI/RA 登录 → 候选/被试/设备包/异常管理 → GPS 状态汇入统一运营界面。

## 当前结构

```text
LEHUE-Manager/
├── server/
│   ├── app/
│   │   ├── core/
│   │   │   ├── db.py               # 运营 + GPS DB
│   │   │   ├── identity_db.py      # 身份/联系/账号 DB
│   │   │   └── web_security.py
│   │   ├── modules/
│   │   │   ├── gps/                # 已实现
│   │   │   ├── admin/              # v0.3 已实现基础运营
│   │   │   ├── light/              # reserved
│   │   │   ├── questionnaire/      # reserved
│   │   │   └── qc/                 # reserved
│   │   └── web/index.html          # 单页 Web Admin；延续旧 V5 的表格/状态卡思路
│   ├── scripts/
│   ├── tests/
│   └── data/                        # 永远不提交 Git
├── scripts/
├── docs/
├── docker-compose.yml
└── Caddyfile
```

## v0.3.0 新增

- 基础 Web Admin 可视化界面：总览、被试运行、候选池、设备包、异常、数据源、系统架构。
- 保留旧 V5 的候选→赋 ID/预约→启动→异常处理核心工作流。
- PI / RA 登录；12 h HttpOnly session；CSRF；安全响应头；audit log。
- 身份库 `lehue_identity.sqlite3` 与伪匿名运营/GPS库 `lehue.sqlite3` 物理分离。
- Web 中可为正式被试一次性生成 OwnTracks credential；secret 仍只存 hash。
- GPS 最近回传直接进入 Dashboard / 被试运行表。
- Lighting、问卷星、AX3 以“数据源”方式预留，不硬搬旧 V5 的本地扫描实现。
- `migrate_v5_state.py` 可迁移旧候选、被试、设备包和异常。
- 自动测试覆盖原 GPS API 和基础 Web Admin workflow。

## 第一次 Windows 本地运行

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
.\scripts\windows_create_admin.ps1 -Username pi -Role pi -DisplayName "PI"
.\scripts\windows_start.ps1
```

浏览器：

- Web Admin: `http://127.0.0.1:8085/admin`
- Health: `http://127.0.0.1:8085/health`
- API docs: `http://127.0.0.1:8085/docs`

详细见 `docs/03_WEB_ADMIN_V0_3.md`。

## 安全边界

- `.env`、两个 SQLite、raw JSONL、GPS、真实联系方式均不得提交 Git。
- 公网部署只暴露 Caddy 80/443；FastAPI 8000 不直接开放。
- `/health` 不返回 participant ID、身份信息或坐标。
- v0.3 是工程测试级 Web Admin；正式被试前仍需冻结自动备份/恢复、账号权限细分、数据地域与隐私流程。
