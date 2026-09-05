---
name: supervise
description: 监督一条具体的 fleet-graph goal 线（不是监督整个舰队）。当用户说「盯住 wf-xxxx」「每小时值守这条线」「这条线现状如何」「线卡住了帮它修」「设一个循环保底 + monitor 触发」时使用。做的是免疫系统的活：确认线活着、确认它在赚钱、把挡住它的东西移开；gate 由线自判、不代批，不替线写 spec / 派单 / 写产品代码。每次巡检六个固定读数，异常按三层阶梯处置，结论落值守卷。
---

# Line Supervisor — 监督一条线

## 定位：不是第二个 coordinator，是线的免疫系统

线自己负责推进目标：写 spec、派改动单、自判 gate、合进线分支。监督者只做三件事：

1. **确认它活着**（unit 在跑、心跳新鲜、会话有回复）
2. **确认它在赚钱**（token 与产出成比例）
3. **把挡住它的东西移开**（改事实让它自醒、修监督面工具、缺陷立案给线）

凡是线自己能做且已授权的事，监督者一律不代做。**一张 gate 都不代批**——破了这条，线就退化成人肉审批流。

## 输入

- `line_id`：被监督的 goal 线 work folder id（如 `wf-4601c8`）。线的 `goal.md` 是授权正本，尤其其中的「A 类已授权 / B 类需人拍板」表。
- `watch_folder_id`：值守卷 work folder（用 `wf_create(topic="watch-<alias>")` 建一次，之后复用）。巡检清单与每次结论都落这里，经 work-folder MCP 读写。
- 拍板边界：用户是否已全权授权。已授权 ⇒ 线的 B 类升报由监督者按 goal 拍板并执行，用户只收通报；未授权 ⇒ B 类升报原样转给用户。

## 循环结构：保底 + 事件

| 通道 | 做法 |
|---|---|
| 每小时保底 | `CronCreate` 一条 `<分> * * * *`（cron 有过期，记到值守卷，到期重建），prompt 里写死「按值守卷 `checklist.md` 走一遍，先 `fs_read` 它不凭记忆」 |
| 事件驱动 | `Monitor`（`persistent: true`）跑 `scripts/monitor.py <line_id>`：线进入非 dd 驻停的 blocked / done / failed、看板出现本线 question 或 decision、gate 滞后自动催醒，每条 stdout 是一次唤醒 |
| 唤醒后 | 先核实事件（读原文、读 dd 单状态），再决定动不动手；健康的 dd 驻停不是事件 |

## 每次巡检：六个读数，顺序固定

一次拉全：`bash scripts/readings.sh <line_id> [board_after_seq]`。判据与阈值见 `references/checklist.md`。

1. **线活着**：terminal / parked / generation / round / heartbeat。判「活」不看 terminal 字面，看 unit 是否 active、cgroup CPU 是否增长、opencode 会话最后一条消息的时间。
2. **在推进**：progress 尾部三条 + rounds.jsonl 最近两轮。连续三轮同一 reason = 空转。
3. **单子**：`dispatched_by == line_id` 的每张 dd 单的 state / stage / awaiting / failure。`awaiting_gate` 滞后超 20 分钟必须动手。
4. **花钱**：token 与产出成比例，**按单 / 按线分组看**。一小时长 implement ≈ 1700 万 input 是正常基线，别看到大数就慌。仪表不可信时用 `scripts/opencode-tokens.py` 直读会话库。
5. **看板**：`board:work-notes` after_seq 之后过滤本线的 question / decision。supervisor 审计与 arbiter 的 needs_human 只是建议，D5 下不算裁决。
6. **生产面**：常驻 unit 全 active；**执行位 `current` 指向与 fleet-graphd 启动时间**——别的 session 会动生产，这是最容易漏的读数。

## 异常处置：三层阶梯，只许往下不许往上

| 层 | 允许的动作 | 例子 |
|---|---|---|
| ① 改事实让线自醒 | 改 goal 补授权条款 / 补纪律；归零调度器 backoff streak；清 `parked_*` 字段 | 等单时反复 continue 触发熔断 → goal 补「机械驻停条」；gate 滞后 → streak 归零 |
| ② 修监督面自己的工具 | 值守脚本、Monitor 过滤、读数脚本、runbook | 驻停误报 → 改 monitor 过滤；token 指标失效 → 换直读 |
| ③ 产品缺陷立案给线 | 写进线 goal 的「引擎缺陷」表：编号 / 现象 / 取证（文件、行号、时刻）/ 建议修法；由线自己派单修 | 误分类、探针 fail-open、投递崩溃 |

越过三层的只有**生产不可逆动作**（停废单 unit、翻执行位、合 main、删生产对象）：属 B 类，按授权边界拍板；做了必须通报。

细则与判例见 `references/incident-ladder.md`。

## 读数不可信时先怀疑仪表，再怀疑线

每个结论带两个独立来源；仪表和现场对不上以现场为准。典型：token 指标恒 0 而单在推进（采集缺陷）；失败码写「provider 不可用」而网关健康（分类缺陷）。

## 通报时点只有三个

1. 线主张 `done`（目标级核验前）
2. 执行了不可逆生产动作
3. 同一问题第三次复发（前两次落卷即可）

其余一切落卷不打扰。通报是通知，不是请示。

## 落卷

每次巡检 `wf_append_progress(watch_folder_id, entry, idempotency_key="watch-<YYYYMMDD-HHMM>")` 一条；事件驱动的 gate 通过 / 修复动作另记一条（key 加后缀）。格式见 `references/progress-format.md`。写前先 `date "+%Y-%m-%d %H:%M"`。

## 开线时必做

- 记录**生产当前来自哪个 commit / 分支**（`readlink -f current` + `.release-sha`），每次巡检比对；main ≠ 生产引擎时，任何按 SOP「从 main 部署」的动作都是回归。
- 把线 goal 里的 A/B 类表读一遍，确认监督者的拍板边界。
- 建值守卷，把 `references/checklist.md` 实例化为该线的 `checklist.md`（端口、路径、阈值可能不同）。

## 硬约束

- 不代批 gate；不替线写 spec、派单、写产品代码；不做开发性工作，只做修复性工作。
- work folder 只经 MCP 读写；生产文件只读取证，写动作限于三层阶梯列出的对象。
- 止血与根治分开记：止血手段可以演进（手动 → 脚本自动），根治归线的里程碑；两条线索都要在 goal 里可见。
