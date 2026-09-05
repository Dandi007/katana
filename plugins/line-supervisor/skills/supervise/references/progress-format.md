# 值守卷落卷格式

每次触发一条，`wf_append_progress(folder_id=<watch>, entry=..., idempotency_key=...)`，写前先 `date "+%Y-%m-%d %H:%M"`。

## 保底巡检（key = `watch-<YYYYMMDD-HHMM>`）

```
【值守 · YYYY-MM-DD HH:MM】线：terminal / gen / round / heartbeat（一句话说明为什么算正常或不正常）；调度器：streak / parked / 下次重燃时刻。里程碑：新合流的 commit、新冻结的 spec、新派的单。单：N 张（complete a，refused b 已接替，running c——活性亲查：unit 起始时刻、CPU、会话最后消息时刻）。花钱：来源 + 数字（按单 / 线分组）+ 与产出的比例判断；cost_obs 变化。看板：head_seq，新 question / decision 及归属。生产面：常驻 unit 状态；执行位指向是否变化。动作：做了什么或无。下一步：下小时看什么、阈值触发做什么。
```

## 事件（key = `watch-<YYYYMMDD-HHMM>-<event>`，如 `-gate3`、`-blocked-fix`、`-snapshot`）

```
【值守 · HH:MM · <事件名>】Monitor 报 <原文摘要>。核实：<decided_by / 状态 / 出处>。<线自判 / 非代批 / 修了什么>。动作：<无 | 具体动作>。下一步：<...>。
```

## 通报用户（回复正文，不入卷也可）

只在三个时点：线主张 done；执行了不可逆生产动作；同一问题第三次复发。一段话：发生了什么、影响是什么、我做了什么、根治在哪。通知，不是请示。

## 缺陷立案（写进线 goal 的缺陷表，不是值守卷）

`| 编号 | 缺陷一句话 | 取证（文件:行、时刻、原文片段、对照的健康读数） | 建议修法 + 紧急度 |`
