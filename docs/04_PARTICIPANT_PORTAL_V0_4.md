# LEHUE Participant Portal v0.4

v0.4 的目标不是做通用问卷平台，而是把本研究的**被试每日工作入口**并入 LEHUE。

## 1. 专属链接

在 Web Admin → 被试运行 中点击“生成工作入口”。系统返回：

```text
https://你的域名/p/<随机 token>
```

- URL 不包含三位数 participant ID。
- token 由 selector + secret 组成；服务器先按 selector 定位，再校验 secret hash。
- secret 明文只在生成时返回一次，数据库只保存 salted hash。
- “重置工作入口”会生成新 token，旧链接立即失效。
- 链接本身等价于被试端身份凭据，请勿公开或转发。

## 2. 被试看到什么

页面沿用 ANOLighting 的移动端单卡片/任务列表思路：

- 当前实验日与 14 日进度；
- 晨间睡眠记录；
- 睡前状态记录；
- GPS 最近回传状态；
- 后续 Lighting 上传等任务可继续放进同一个列表。

被试不填写姓名、手机号或 participant ID。

## 3. 问卷数据

系统不建立通用问卷设计器。自 v0.5.1 起，晨间/睡前两份正式问卷由独立的 `server/app/modules/questionnaire/forms.py` 模块定义和校验；Participant Portal 只负责调用与通用渲染。每份问卷在一个纵向页面内完整显示，不分页。

数据库只新增一张：

```text
questionnaire_responses
- participant_id
- date_local
- study_day
- form_key
- form_version
- answers_json
- submitted_at_utc
```

唯一约束 `(participant_id, date_local, form_key)`，因此同一被试同一天同一问卷默认只接受一次最终提交。

## 4. 与现有系统的关系

```text
Participant /p/<token>
       │
       ├── Native Questionnaire ──┐
       │                          │
OwnTracks GPS ────────────────────┤
                                  ▼
                         lehue.sqlite3
                                  │
                         Web Admin status
                                  │
                         Local scientific QC
```

身份联系方式仍只在 `lehue_identity.sqlite3`；问卷响应与 GPS、设备、运营状态一起放在伪匿名运营库。

## 5. 这批联调怎么测

1. Admin 新建/启动一个测试被试；
2. 创建 OwnTracks GPS credential；
3. 创建专属工作入口并在手机打开；
4. 确认页面显示 GPS 状态；
5. 填晨间/晚间测试问卷；
6. 刷新 Admin，检查“今日问卷 1/2、2/2”；
7. 重复提交同一问卷应被拒绝；
8. 重置工作入口后，旧链接应失效，新链接应正常。

## 6. 正式实验前仍需确认

- 确定是否设置晨间/晚间开放时间窗；
- 确定错填后的“允许重填/管理员作废”流程；
- portal/API 登录限速；
- 自动异常：规定时间仍未提交时自动进入 Incident Center；
- 隐私说明与伦理文本。
