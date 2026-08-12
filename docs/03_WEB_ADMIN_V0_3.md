# LEHUE Web Admin v0.3.1

v0.3.1 只搬 **实验运营与采集状态**，科研分析继续留在本地。

## 1. 本地启动

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
.\scripts\windows_start.ps1
```

打开 `http://127.0.0.1:8085/admin`。

- 若尚无管理员：页面进入“初始化管理员”，直接创建首个 PI。
- 若已有管理员：只显示登录页，不存在公开注册入口。

## 2. 服务器首次初始化

公网不开放浏览器首次注册。ECS 首次部署运行 `bash scripts/server_setup.sh test`
并只输入 PI 用户名/密码；内部密钥自动生成。首个 PI 创建后仍由数据库事务保证
只能初始化一次，仅凭网址无法创建账号。本地 loopback 开发仍可从页面创建首个 PI。

`windows_create_admin.ps1` / `bootstrap_admin.py` 仍保留为故障恢复工具，但不再是正常注册流程。

## 3. PI / RA 权限

PI 在“系统”页可以：

- 新增 PI 或 RA；
- 自填密码，或留空让系统生成一次性强密码；
- 禁用/启用其他账号；
- 重置密码；
- 下载系统状态备份。

RA 可以使用日常实验运营页面，但不能管理账号或下载包含身份/GPS的系统备份。系统禁止当前 PI 自己禁用自己，也禁止禁用最后一个有效 PI。

## 4. State Bundle

“系统 → State Bundle”与定时 OSS 备份、TEST 跨机迁移使用同一个格式。下载包包含两份 SQLite、GPS raw JSONL、manifest 和 credential 元数据；Lighting raw 不复制，只保存私有 OSS bucket/object key/SHA256/size 引用。

PI 可上传 State Bundle 整包替换当前状态。系统先校验 ZIP、SQLite、版本与 TEST/PROD 环境并生成 rollback，再用目标服务器现有 Fernet key 重新加密 GPS/Portal credential。服务器 `.env`、DOMAIN、RAM Role、OSS endpoint 和 `ADMIN_TOKEN` 不被覆盖。包内若有仅 local 可用的 Lighting raw 引用，则不能 apply 到 OSS 环境。

State Bundle 会清除 `web_sessions`，因此恢复后所有人需要重新登录；账号及原密码仍有效。State Bundle 含敏感身份、定位和 credential 信息，必须按密钥材料保管。

## 5. 当前页面

- 总览：候选、预约、运行、开放异常、可用设备包、已有 GPS 被试。
- 被试运行：三位数 ID、日期、设备包、RA、GPS 最近回传；可启动运行和生成 OwnTracks 凭据。
- 候选池：保留旧 V5 的候选→正式 ID/预约逻辑。
- 设备包：以 pack 为最小周转单元，预留 Lighting / AX3 serial。
- 异常：执行期异常中心。
- 系统（PI）：账号管理与系统状态备份。

## 6. 从旧 V5 state.json 迁移

```powershell
.\.venv\Scripts\python.exe scripts\migrate_v5_state.py "D:\path\to\old\data\state.json"
```

迁移 candidates、subjects、devices、issues；不迁移旧问卷缓存、文件 manifest、daily_records 和 scan_index。
