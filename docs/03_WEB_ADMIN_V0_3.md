# LEHUE Web Admin v0.3

v0.3 只搬 **实验运营与采集状态**，不搬科研分析。

## 1. 本地启动

第一次：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
.\scripts\windows_create_admin.ps1 -Username pi -Role pi -DisplayName "PI"
.\scripts\windows_start.ps1
```

浏览器打开：

- `http://127.0.0.1:8085/admin`

服务器部署后则是：

- `https://你的域名/admin`

## 2. 当前页面

- 总览：候选、预约、运行、开放异常、可用设备包、已有 GPS 被试。
- 被试运行：三位数 ID、日期、设备包、RA、GPS 最近回传；可启动运行和生成 OwnTracks 凭据。
- 候选池：保留旧 V5 的候选→正式 ID/预约逻辑。
- 设备包：以 pack 为最小周转单元，预留 Lighting / AX3 serial。
- 异常：执行期异常中心；只处理需要人介入的事项。
- 数据源：GPS / Lighting / Questionnaire / AX3 / Identity 的接入方式、存储和自动化状态。
- 系统架构：用业务语言显示云端管理、各数据源、数据库、OSS 与本地科研工作站之间的边界。

## 3. 两个数据库

`server/data/lehue.sqlite3`

- 伪匿名运营状态；
- GPS credential hash / raw event / standardized location；
- study_subjects；
- device_packs；
- incidents；
- audit_log。

`server/data/lehue_identity.sqlite3`

- admin users / sessions；
- candidate identity / contact information；
- contact_logs（表已预留，UI 后续再接）。

二者仍由同一个 Web Admin 使用，但物理文件分离，便于未来独立备份、权限与迁移。

## 4. 从旧 V5 state.json 迁移

先备份旧系统，然后在 `server` 目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_v5_state.py "D:\path\to\old\data\state.json"
```

会迁移：

- candidates；
- subjects → study_subjects；
- devices → device_packs；
- issues → incidents。

故意不迁移：旧问卷解析缓存、Lighting/GPS 文件 manifest、daily_records、scan_index。它们属于旧的“本地扫描一切”数据链，后续分别由 Questionnaire adapter、OSS/Lighting adapter、GPS realtime ingest 重建。

## 5. 权限

第一版支持 `pi` 和 `ra` 账号；账号从服务器脚本创建，不在网页公开注册。

```bash
python scripts/bootstrap_admin.py pi --role pi --display-name "PI"
python scripts/bootstrap_admin.py ra01 --role ra --display-name "RA 01"
```

密码只显示一次，数据库只存 salted PBKDF2 hash。Web 使用 12 小时 HttpOnly session cookie，写操作还要求 CSRF token。

v0.3 暂未细分 PI 与 RA 的每一个按钮权限；角色字段和审计日志先落地，后续在真实协作流程明确后再收紧，避免现在过度设计。
