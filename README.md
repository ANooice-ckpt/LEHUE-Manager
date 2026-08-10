# LEHUE-Manager v0.5.3

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
│   │   │   ├── light/              # Lighting 导入与 acquisition QC
│   │   │   └── qc/                 # reserved
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
- 被试打开专属链接后无需填写姓名或被试号，服务器自动绑定 participant、研究日期与 Study Day。
- Participant Portal 参考原 ANOLighting 的移动端单卡片/任务列表结构，接入正式晨间/睡前问卷、Lighting 上传和 GPS 回传状态。
- 问卷不再依赖问卷星/每日 CSV 下载；答案直接写入 `lehue.sqlite3` 的 `questionnaire_responses`。
- 两份正式问卷由独立 `questionnaire/forms.py` 模块提供，均在一个移动端页面内完成，不建设复杂问卷设计器。
- Admin 的被试表与 Dashboard 可看到今日问卷完成数；数据源页将 Questionnaire 标记为 LEHUE native connected。
- 旧 v0.3.x 数据库可原地升级：启动时自动增加 portal token 字段并创建问卷响应表，不清空旧数据。
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
- 当前仍是工程测试版本；正式被试前还需补登录/portal 限速、定时异地备份、恢复演练及隐私流程。
