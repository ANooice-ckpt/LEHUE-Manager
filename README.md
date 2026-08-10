# LEHUE-Manager v0.4.0

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
│   │   │   ├── participant/        # v0.4 被试专属入口 + 每日问卷
│   │   │   ├── light/              # reserved
│   │   │   └── qc/                 # reserved
│   │   └── web/                    # Admin + Participant Portal 页面
│   ├── scripts/
│   ├── tests/
│   └── data/                        # 永远不提交 Git
├── scripts/
├── docs/
├── docker-compose.yml
└── Caddyfile
```

## v0.4.0 当前能力

- 保留 v0.3.1 的 Web Admin、PI/RA 登录、非公开账号初始化、系统状态备份。
- 每个正式被试可在 Admin 中生成一个不可猜测的专属工作入口；重置入口后旧链接立即失效。
- 被试打开专属链接后无需填写姓名或被试号，服务器自动绑定 participant、研究日期与 Study Day。
- Participant Portal 参考原 ANOLighting 的移动端单卡片/任务列表结构，首批接入晨间问卷、晚间问卷和 GPS 回传状态。
- 问卷不再依赖问卷星/每日 CSV 下载；答案直接写入 `lehue.sqlite3` 的 `questionnaire_responses`。
- 第一版问卷题目是 **系统联调测试版**，直接由代码配置，不建设复杂问卷设计器。
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

## 安全边界

- `.env`、两个 SQLite、raw JSONL、GPS、问卷响应、真实联系方式均不得提交 Git。
- `/p/<token>` 的 token 本身就是被试端身份凭据：不可猜测、数据库仅存 hash，但收到链接的人可以代表该被试访问工作入口，因此不要转发或公开。
- 公网部署只暴露 Caddy 80/443；FastAPI 8000 不直接开放。
- `/health` 不返回 participant ID、身份信息、坐标或问卷答案。
- v0.4.0 仍是工程测试版本；正式被试前还需补登录/portal 限速、定时异地备份、恢复演练、正式问卷冻结及隐私流程。
