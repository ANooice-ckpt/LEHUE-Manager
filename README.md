# LEHUE-Manager v0.5.4

**LEHUE = Light Exposure Histories in Urban Environments**

LEHUE-Manager 是实验运营与采集状态基础设施。**科研分析不搬云端。** 当前实现三条主线：

1. GPS acquisition：OwnTracks → HTTP/HTTPS → FastAPI → participant authentication → SQLite + append-only JSONL → acquisition QC / CSV export。
2. Web Admin：PI/RA 登录 → 候选/被试/设备包/异常/账号管理 → GPS 与问卷状态汇入统一运营界面。
3. Participant Portal：每个被试一个 `/p/<token>` 专属工作入口 → 自动识别身份与 Study Day → 完成每日问卷并查看 GPS 回传状态。

## 当前结构

```text
LEHUE-Manager/
├── server/
│   ├── app/
│   │   ├── core/
│   │   │   ├── db.py               # 运营 + GPS + Questionnaire DB
│   │   │   ├── identity_db.py      # 身份/联系/账号 DB
│   │   │   └── web_security.py
│   │   ├── modules/
│   │   │   ├── gps/                # 已实现
│   │   │   ├── admin/              # Web Admin
│   │   │   ├── participant/        # 被试专属入口与任务编排
│   │   │   ├── questionnaire/      # 可独立调用的正式问卷定义与校验
│   │   │   └── light/              # Lighting 导入与 acquisition QC
│   │   └── web/                    # Admin + Participant Portal 页面
│   ├── scripts/
│   ├── tests/
│   ├── test_seed/                   # Git 跟踪的纯模拟 TEST 初始数据
│   └── data/
│       ├── test/                    # TEST 运行数据，不提交 Git
│       └── prod/                    # PROD 正式数据，永不提交 Git
├── scripts/
├── docs/
├── docker-compose.yml
└── Caddyfile
```

## 当前能力

- 保留 v0.3.1 的 Web Admin、PI/RA 登录、非公开账号初始化、系统状态备份。
- 每个正式被试可在 Admin 中生成一个不可猜测的专属工作入口；重置入口后旧链接立即失效。
- GPS 密码和工作入口可在被试列表中随时查看、复制或重置；服务器保留 hash 认证并额外保存加密副本。
- 被试打开专属链接后无需填写姓名或被试号，服务器自动绑定 participant、研究日期与 Study Day。
- Participant Portal 参考原 ANOLighting 的移动端单卡片/任务列表结构，接入正式晨间/睡前问卷、Lighting 上传和 GPS 回传状态。
- 问卷不再依赖问卷星/每日 CSV 下载；答案直接写入 `lehue.sqlite3` 的 `questionnaire_responses`。
- 两份正式问卷由独立 `questionnaire/forms.py` 模块提供，均在一个移动端页面内完成，不建设复杂问卷设计器。
- Admin 的被试表与 Dashboard 可看到当前两个任务问卷的完成数；数据源页将 Questionnaire 标记为 LEHUE native connected。
- 被试端同时显示可修改的“日历日”和自动推导的“实验日 / Study Day”。例如 8 月 15 日 03:00 入睡、08:00 起床时，两份问卷都显示日历日 8 月 15 日、实验日 8 月 14 日；“昨晚”明确显示为“8 月 14 日晚 → 8 月 15 日早”。
- 日期按 `STUDY_TIMEZONE` 归属：睡前问卷匹配实验日，晨间问卷保留次日起床日用于 QC 配对；被试修正日历日后，服务端重新计算实验日并校验其处于实验范围内。
- 为容纳晚睡晚起，前一暴露日默认到本地 18:00 才关闭并判缺；两条边界可分别通过 `QUESTIONNAIRE_EVENING_CUTOFF_HOUR` 和 `QC_DAY_CLOSE_HOUR` 调整。
- 旧 v0.3.x 数据库可原地升级：启动时自动增加 portal token 和问卷日历日字段；问卷响应表不存在时自动创建，不清空旧数据。
- 系统状态备份自动包含问卷响应。

## 第一次 Windows 本地运行

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_setup.ps1
.\scripts\windows_start.ps1
```

然后打开：

- Web Admin: `http://127.0.0.1:8085/admin`
- Health: `http://127.0.0.1:8085/health`
- API docs: `http://127.0.0.1:8085/docs`

在“被试运行”中给被试生成“工作入口”，复制返回的专属链接后即可在手机/浏览器测试问卷和 GPS 状态。详见 `docs/04_PARTICIPANT_PORTAL_V0_4.md`。

## TEST / PROD 数据边界

- 每次启动必须显式选择 `test` 或 `prod`，运行期间不能从页面切换。
- `server/test_seed/` 是允许进入 Git 的纯模拟基线；新的或尚无记录的 TEST 环境会自动复制一次。
- TEST 后续操作只修改被 Git 忽略的 `data/test`，因此多地 `git pull` 不会产生 SQLite 冲突。
- PROD 只读写 `data/prod`，永远不会加载 `test_seed`；正式数据和 `.env` 始终被 Git 忽略。

## 安全边界

- `.env`、TEST/PROD 运行库、raw JSONL、GPS、问卷响应和真实联系方式均不得提交 Git；唯一例外是明确标记为纯模拟的 `server/test_seed/`。
- `/p/<token>` 的 token 本身就是被试端身份凭据：不可猜测、数据库仅存 hash，但收到链接的人可以代表该被试访问工作入口，因此不要转发或公开。
- 公网部署只暴露 Caddy 80/443；FastAPI 8000 不直接开放。
- `/health` 不返回 participant ID、身份信息、坐标或问卷答案。
- 当前仍是工程测试版本；正式被试前还需冻结登录/portal 限速策略，启用并验证定时异地备份，完成恢复演练及隐私流程。

## Lighting raw storage：本地与 OSS

Lighting 原始文件通过 `light/storage.py` 保存。本地 local 模式仍由 FastAPI 流式写入临时文件，canonical 保存成功后首次 QC 直接解析该临时文件；OSS 模式则由浏览器使用短期签名 URL 直传，ECS 只为 QC 下载临时副本。人工重新 QC 同样从 storage 读取 canonical raw。QC 结果写入 `lighting_files`，Daily QC 只查询 SQLite 中已经保存的字段。

对象键固定为：

```text
raw/lighting/<participant_id>/<date_local>/<upload_uid>.<ext>
```

Windows 本地 TEST 默认不需要 OSS：

```powershell
$env:LEHUE_ENV = "test"
$env:LIGHT_STORAGE_BACKEND = "local"
```

此时 canonical raw 位于 `server/data/test/raw/lighting/...`。运行测试：

```powershell
server\.venv\Scripts\python.exe -m pytest server\tests -q
```

云端 TEST 或本地真实 OSS 集成测试使用独立的香港测试桶和最小权限凭据：

```powershell
$env:LEHUE_ENV = "test"
$env:LIGHT_STORAGE_BACKEND = "oss"
$env:OSS_BUCKET = "<TEST bucket>"
$env:OSS_REGION = "cn-hongkong"
$env:OSS_CREDENTIAL_MODE = "access_key"
$env:OSS_ACCESS_KEY_ID = "<secret>"
$env:OSS_ACCESS_KEY_SECRET = "<secret>"
$env:RUN_OSS_INTEGRATION = "1"
server\.venv\Scripts\python.exe -m pytest server\tests\test_light_storage.py -q
```

`OSS_ENDPOINT` 可选，供 ECS 下载 QC 使用内网地址；浏览器直传使用 `OSS_PUBLIC_ENDPOINT`，不设置时由 `OSS_REGION` 生成公网地址。本地 AK/SK 仅用于显式 OSS 集成测试，不写入代码或 Git。

云端 TEST / PROD 设置 `OSS_CREDENTIAL_MODE=ecs_ram_role`，ECS 绑定 RAM Role 后无需配置 AK/SK；PROD 会拒绝 `access_key` 模式。Lighting 与自动备份共用同一个自动刷新临时凭据 provider。TEST 与 PROD 必须使用不同 bucket、RAM 权限和 SQLite 数据目录。

OSS 模式下 Participant Portal 先登记 `pending` 并取得约 15 分钟有效、只允许写入指定 object key 的 PUT URL；浏览器直接上传 OSS，FastAPI 不接收 40 MB 文件。OSS 到件后记录变为 `uploaded`，ECS 临时下载、校验 size/SHA256 并执行现有 QC，成功后状态为 `qc`。中断后重新选择同一文件会复用原 `upload_uid` 继续处理。本地 `test + local` 仍走原上传接口。

直传 bucket 需配置 CORS：允许 Participant Portal 的 HTTPS Origin、`PUT` 方法和 `x-oss-meta-sha256` 请求头。不要使用 `*` Origin 承载正式实验。

## OwnTracks 冻结参数与 Acquisition QC

统一使用 OwnTracks **Move** 模式，目标采样间隔约 10 秒：Android 设置 Move 10 s；iOS 设置 `locatorInterval=10`、`locatorDisplacement=10`、`adapt=0`、`downgrade=0`。这是目标设置，不要求数据严格每 10 秒一个点；OS 后台调度、卫星定位条件、网络中断和恢复补传都会改变实际间隔。

高频 HTTP 鉴权仍使用随机 GPS credential 和数据库中的 PBKDF2 hash。进程只短时缓存已验证的“被试 + 当前 hash + 密码指纹”，默认 300 秒；重置凭据会改变数据库 hash，因此旧缓存立即失效。运行状态在近期收到旧记录时显示“补传中”。Daily GPS QC 只看点数、自然日首末覆盖和明显长断档，不计算轨迹距离或行为指标；阈值可用 `.env.example` 中的 `GPS_DAILY_*` 调整。

## 定时异地备份

`server/scripts/backup_to_oss.py` 使用 SQLite 在线备份 API生成两个数据库的一致副本，并将 GPS raw JSONL 一起压缩后上传到独立私有 OSS bucket。Lighting canonical raw 已在 OSS，不重复复制。对象键自动包含 `test` 或 `prod`，两套运行环境仍应使用不同 bucket 或至少不同 RAM 权限。

配置 `BACKUP_OSS_BUCKET`、`BACKUP_OSS_PREFIX` 后，备份脚本使用同一个 RAM Role credential provider，可先手工验证：

```bash
LEHUE_ENV=test docker compose exec -T api python scripts/backup_to_oss.py
```

再由服务器 cron 定期运行 `bash /opt/LEHUE-Manager/scripts/server_backup.sh`。备份桶必须私有，并定期做恢复演练。

## Admin GPS 轨迹诊断

“被试运行”列表只显示现有 GPS 在线状态、最后回传时间和“轨迹”按钮，不在列表加载时运行完整 GPS QC。轨迹 dialog 支持最近 1 h / 12 h / 24 h，数据只查询 `gps_locations`；endpoint 在一次按时间扫描中同时计算最新记录、总点数、最大间隔和低精度百分比，并完成显示降采样。超过 `QC_GAP_WARNING_SECONDS` 的时段不会用折线跨接。

Leaflet 1.9.4 已保存在 `server/app/web/vendor/leaflet-1.9.4/`，不依赖运行时 CDN。底图可以在 `.env` 中替换：

```env
GPS_TILE_URL=https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png
GPS_TILE_ATTRIBUTION=&copy; OpenStreetMap contributors
```

GPS raw JSONL 文件名按 `STUDY_TIMEZONE` 的自然日生成；JSONL 内部的 `server_received_at_utc` 等时间仍保持 UTC。
