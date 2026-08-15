# 正式每日问卷模块

当前 Portal 承接晨间/睡前日记和 S2 结题；入组问卷改用外部问卷星，不在 LEHUE 中填写，也不作为 Ready 条件。历史数据库中已经存在的旧入组问卷记录保留但不再读取，不影响 Portal、Ready 或日常任务。结束正式曝光后，Portal 继续开放最后 exposure day 的次晨问卷、S2 与必要补传，随后才 completed。日历日由服务器记录为真实提交日，不再要求被试手工修改。

v0.5.1 使用研究提供的两份正式问卷：晨起后填写的“每日记录1”和入睡前填写的“每日记录2”。原表中的编号、日期/日间活动日期不在页面中重复询问，由 `/p/<token>` 对应的 participant、服务器本地日期和 Study Day 自动绑定。

## 模块边界

问卷定义与校验位于：

```text
server/app/modules/questionnaire/forms.py
```

该模块不依赖 FastAPI 或数据库，对外提供三个小接口：

```python
from app.modules.questionnaire import get_form, list_forms, validate_answers

forms = list_forms()
morning = get_form("morning")
answers = validate_answers(morning, submitted_answers)
```

Participant service 负责 token、participant ID、日期、Study Day 和持久化；HTML 页面只根据题型通用渲染。这让后续导出、离线工具或其他入口可以复用同一份正式定义，而不复制题目和校验逻辑。

## 页面与答案规则

- 每份问卷在一个移动端纵向页面中完整呈现，不设置步骤或翻页。
- 正式版本号为 `formal_v1`；数据库继续使用现有 `questionnaire_responses` 表，不增加问卷表。
- 选择题保存稳定英文代码，显示文案可以独立调整而不破坏既有答案。
- `-3` 到 `3` 量表使用七个离散单选项，不预选答案。
- “没有”与其他睡眠影响选项互斥，前后端均处理，服务器校验是最终约束。
- 设备状态矩阵要求 GPS 和光照两行都填写。
- 同一 participant、日期和问卷仍只接受一次最终提交。
- 页面将“日历日”和“实验日”分开显示。日历日是实际入睡/起床所在的自然日期，被试可在提交前修正；实验日和 Study Day 由系统重新计算，用于 Daily QC。
- 晨间问卷的“昨晚”显示为明确的跨日范围，例如“8 月 14 日晚 → 8 月 15 日早”；睡前问卷的“今天白天”显示为实验日起床后至本次入睡前，即使入睡发生在次日凌晨也仍归该实验日。

## 当前正式题目

- 晨间：入睡/醒来时间、当前警醒度、六项睡眠评价、睡眠影响因素，共 10 个可见问题。
- 睡前：当前警醒度、精力/情绪/激活度、午睡时长、GPS 与光照设备状态，共 6 个可见问题。

原问卷题号保留用于现场核对，因此页面从第 3 题开始；第 1、2 项已由系统自动绑定。
