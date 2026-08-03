# loop-orchestration —— 当前实现（Loop MCP 驱动）

> **⚠️ 同目录上层的 `workflow.js` 是被本实现取代的旧版**，不是现行实现。
> 旧版：JS 编排脚本按**轮次**驱动，每轮并发派探索者 → 全部返回后 barrier 同步 → 裁决 → 下一轮。
> 本版：Loop MCP 定时触发 → coordinator subagent 逐 turn 读板派发，**无 barrier**。

## 这是什么

`deep-research` 的编排面。由 Loop MCP 的 cron trigger 作为 callback 拉起 `coordinator-turn.sh`，
它只做机械动作（拉起 coordinator subagent 跑一个 turn、看哨兵），**一切研究判断在 coordinator 内**。

| 文件 | 职责 |
|---|---|
| `coordinator-turn.sh` | 一个 turn 的入口。stdin 收 `loop-callback/1` envelope；落 `state/DONE` 哨兵后调 `loop_complete` |
| `coordinator-prompt.md` | coordinator 的章程：读板 → 按护栏派发 → 判收敛 |
| `roles/worker.md` | 检索取证，产出 finding / excerpt / 新线索 |
| `roles/triage.md` | 新线索取舍去重 + 证据覆盖度判断 |
| `roles/synthesizer.md` | 综合成稿 |
| `loop_complete.py` | 向 Loop MCP 声明 goal 达成 |

## 出处与既往战绩

2026-08-02 03:05Z–04:48Z，本实现完成 deep-research **首次全自动实跑**：
11 个 turn、18 张线索、27 条 finding、3 轮裁决，产出 42.9KB 研究报告，
其结论闭环到一个生产修复 PR 并上线。现场与复盘见 work folder `wf-ffca49`。

## 为什么现在才进版本控制

**它此前只存在于 `/data/deep-research/loop-mcp-semantics/`，不在任何 git 仓内**（2026-08-03 发现）。
即：跑通唯一一次端到端研究的代码没有版本控制，而版本控制里那份是它取代掉的旧实现。
本次为**纯保全性收录**——原样拷入，未做任何改动。

> `coordinator-turn.sh` 里的 `DIR=/data/deep-research/loop-mcp-semantics` 是运行实例路径，
> 收录时保持原样以确保与实跑逐字一致；若要复用需参数化，那是后续改动，不在本次范围。

## 设计 SSoT

本实现正在被重构为「零专有基建」形态（四个角色声明 + 一个调度图 + 四个协议）。
设计与能力对账见 work folder `wf-dc0c15` 的 `spec.md`；本目录是重构前的**现行可运行版本**。
