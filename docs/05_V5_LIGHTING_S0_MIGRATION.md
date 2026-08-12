# LEHUE v0.5：旧 V5 对照、Lighting 与 S0 迁移

## 1. 迁移边界

旧 ANOLighting V5 被视为经过实际实验验证的 workflow/reference implementation。本次没有恢复旧 GPS 文件扫描，也没有恢复每日问卷星 CSV：

- GPS 继续使用 OwnTracks → FastAPI → SQLite + raw JSONL。
- 每日晨间/晚间问卷继续使用 `/p/<token>` 原生入口，自动绑定 participant、日期和 Study Day。
- Lighting 迁移旧解析、命名识别、有效性与每日状态规则，并改为适合线上服务的完整文件上传。
- S0 继续导入人工下载的问卷星累计 CSV/XLSX，数据放在身份库。
- AX3 与科研统计分析仍保持离线/本地。

没有建立通用问卷设计器、任务引擎、独立 QC 数据库或对象存储抽象层。新增表只有 `lighting_files` 与 `s0_imports`。

## 2. 新旧对应关系

| 旧 V5 | LEHUE v0.5 | 说明 |
|---|---|---|
| `state.forms.candidate_raw` + `state.candidates` | identity DB：`s0_imports` + `candidates` | 原始 S0 文件留档；候选稳定合并 |
| `state.subjects` | operations DB：`study_subjects` | 保留三位 ID、排期、运行/结题、设备包、有效日 |
| `state.devices` | `device_packs` | pack 仍是最小周转单元 |
| Lighting 文件夹扫描与 `scan_index` | raw/lighting + `lighting_files` | HTTP 上传完成后解析，不再需要“30 秒文件稳定”等待 |
| `daily_records` | `/api/v1/web/daily-qc` 动态派生 | 避免重复保存可重算状态；运行核查时同步异常与 `valid_days` |
| `issues` | `incidents` | 自动 acquisition QC 使用确定性 incident UID |
| 旧 GPS 文件 manifest | OwnTracks tables + JSONL | 不迁移旧 GPS 解析器 |
| 旧每日问卷 CSV cache | `questionnaire_responses` | 不迁移旧每日问卷导入器 |

## 3. Lighting 规则

- 文件类型：CSV、XLSX、TXT。
- participant 与实验日只由 Portal 入口和所选任务确定；原文件名仅保存为 provenance，不参与身份、日期或上传判断。
- 同时支持旧设备的重复键值布局与标准表格布局。
- 每日期望 7,200 条；Photopic Lux、Melanopic 均为有限数值且未饱和才计为有效。
- 有效率按 `valid / 7200 × 100` 保留 1 位小数；达到 90.0% 为 `valid`，否则为 `insufficient`；无法识别记录为 `unreadable`。
- 数据质量不阻止 raw 保存。若内容中的 `Modify Time`/已有时间字段均可可靠解析，且整份文件明显不属于实验日或其跨午夜次日，上传仍成功，并通过同一 QC/incident 结果提醒被试与 PI/RA。
- 同一被试同一天可重新上传；QC 依次选择 valid、insufficient、unreadable，并在同质量下选择有效率更高、上传更新的文件。
- raw 文件不进入系统状态 ZIP；SQLite 中的文件元数据、QC 摘要会进入 ZIP。生产部署必须另外备份 persistent volume，未来可直接迁移至 OSS。

## 4. S0 累计表导入

Admin → 候选池 → “S0 问卷星累计表导入”，选择 `.csv` 或 `.xlsx`。

- 当前招募问卷所有答卷均进入候选池，参与意愿作为运营筛选字段，不提前丢弃原始事实。
- 合并优先级为问卷星序号、规范化手机号、规范化微信号。
- 人口结构、日程身份、固定位置比例、室内日光机会、户外时间、屏幕时间、地区、通勤、手机及参与条件直接从 S0 更新；姓名、电话、微信若已人工校正则保留。
- 旧 `light_type` 暂停使用；联合类别仅由固定位置比例派生“固定位置主导/非固定位置主导”，并由室内自然光充足度派生“日光可达/日光受限”。
- 已赋 participant ID 的候选即使不在新累计表中也不会丢失；其他历史候选保留并标记“不在最新累计表”。
- 同一文件按 SHA-256 防止重复导入；原始文件保存在 identity DB 的 `s0_imports`，随敏感系统备份一起保存。
- 旧 `.xls` 不支持；请从问卷星导出 `.xlsx` 或另存为 `.csv`。

## 5. 每日 acquisition QC

Admin → 每日 QC → “运行核查并同步异常”。沿用旧版暴露日定义：

```text
暴露日 d 完整 = d 当晚问卷 + d Lighting + d GPS + d+1 晨间问卷
```

今天的记录保持 `pending`，不会提前生成缺失异常。过去日期按缺晚问卷、Lighting 未上传/样本不足/无法解析、缺 GPS、缺次晨问卷分别生成异常。重新运行后，已经补齐的自动异常直接归档为 `closed`；人工归档的异常不会被强制重开。

## 6. 旧状态迁移

基础迁移：

```powershell
cd server
.\.venv\Scripts\python.exe scripts\migrate_v5_state.py "D:\path\to\old\data\state.json"
```

脚本可重复运行：候选、被试、设备包、异常按稳定 ID upsert。旧 Lighting 目录不再通过文件名推断 participant/date；请从对应 Portal/Admin 实验日上传，使业务上下文成为唯一 canonical 来源。

## 7. 历史兼容性检查

只读检查命令：

```powershell
cd server
.\.venv\Scripts\python.exe scripts\validate_legacy_compat.py --state-json "D:\path\to\state.json" --light-dir "D:\path\to\light_files" --s0 "D:\path\to\S0.xlsx"
```

当前历史测试集结果：26/26 Lighting 缓存摘要完全一致（14 valid、10 insufficient、2 unreadable），真实 S0 XLSX 与 15 行累计 CSV 均可读取。
