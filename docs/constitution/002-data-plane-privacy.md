# 002 — 数据面私有（三域 data root 只经 MCP）

**Status:** Active
**Scope:** `memory` / `wiki` / `work-folder` 三域的 data root，以及一切访问它们的进程
**拍板:** 用户，2026-08-14
**约定:** 本文件记录**硬线**——违反即 REJECT。开发纪律见 [001](001-development-discipline.md)。

## 缘起

2026-07-15 的 KB MCP 统一数据层 cutover 立了一条不变量，**INV-1 Zero mount**：

> 三域数据物理解耦到独立 data repo，藏在各自 MCP 服务后面，**任何 client 都不感知物理路径、不挂载 data root**。

设计对，但它**只是一句君子协定**——直到 2026-08-14 实测：

| | 现状 |
|---|---|
| 三个 data root | `uther:uther 0775` |
| 三个 MCP 服务 | 也跑在 `uther` |
| 结论 | MCP、它要保护的数据、以及所有 agent 是**同一个用户**，零强制 |

代价可量化：某条工作线的观测进程把 `--outdir` 指进受治理的仓（每 20 秒一张 snapshot，计划跑 24 小时），data repo 因此长期处于脏态，work-folder MCP 因 clean-repo 前置条件 **12 小时内拒绝 150 次**；近 7 天 1636 笔 commit 里 **262 笔绕过 MCP 直接 `git commit`**；还出现了人工「代提交脏文件」的扫地 commit。

**症状被感知了很久，一直在打补丁，没治因。** 本条把 INV-1 从设计注记升格为可判违的硬线。

## 第一条　data root 是 MCP 进程的私有成员

三域 data root 的**唯一合法写者是对应的 MCP 服务进程**。其余任何进程——agent、脚本、cron、人——只能经 MCP 的工具面（`wf_*` / `wiki_*` / `memory_*` / `fs_*`）读写。

强制点放在**资源**上，不放在调用方上。沙盒每个写者是枚举法，要挡住所有 agent／runtime／脚本／cron，漏一个就前功尽弃；改数据所有权是结构法，内核替你说不。兑现手段：专用 uid 持有 data root + `0700`，MCP 以该 uid 运行（`agent-shell/bin/katana-seal-data-root`）。

**怎么算违宪。** 用原生 `Write`/`Edit`/`git commit` 直接改 data root，算。让任何非 MCP 进程把产物写进 data root（包括 `--outdir`、日志落点、临时文件），算。为绕开本条而给 agent 开任何形式的 data root 直写后门，算——留了后门就等于没封。

## 第二条　data root 只存结论，不存运行时产物

进 data root 的必须是**经 MCP 落账的结论与状态**。运行时与证据产物——高频写入、大体积、可重生的——落 runtime root，data root 里只保留**引用**（相对路径 + sha256 + 一行摘要）。

判据一句话：**这个文件丢了能不能重新生成？** 能 ⇒ 它是运行时产物，不该在这里。

这条不是洁癖。实测那条工作线的 work folder 里有 `__pycache__`、`*.out`、`*.log`、20 秒一张的 trace snapshot——**data repo 被当工作目录用了**，而 governed 事务模型（每次 mutation 要求作用域内 clean）与「持续有进程往里写」在结构上不相容。

**怎么算违宪。** 把 `__pycache__` / `*.pyc` / 构建产物 / 运行日志 / 高频快照提交进 data root，算。用 `.gitignore` 掩盖而非移走——也算：kernel 连 "ignored untracked payload outside runtime state" 都拒，它要的是仓真干净，不是 git-clean。

## 第三条　爆炸半径匹配作用域

governed mutation 的 clean 前置条件，必须**只覆盖该 mutation 实际触及的范围**（`scope_prefixes` + 控制面），不得因无关路径的脏状态阻断。

这条独立于第一、二条成立：即使写者行为完全合规，一个 mutation 的失败面也不该大于它的作用面。

**怎么算违宪。** 新增 governed op 时漏传 `scope_prefixes`，让它退回整仓检查，算——实测教训：`save` / `resume` / `append progress` / 全部 `fs_*` 都传了 scope，唯独 `create` 漏了，于是任何一个 folder 的脏改动阻断**所有** session 建新 folder（PR #127 修复）。

例外：`reindex` 这类**本来就是全仓**的操作，整仓 clean 是对的，不适用本条。

## 第四条　封住的东西要有备份和运维通道

1. **备份**：data root 封为私有后仍是单点。三域必须有可用的 mirror 与**演练过的**恢复路径；封仓后备份作业以 MCP 的 uid 运行。
   （立此条时的实况：三仓**没有任何 remote**，work-folder 域 728M / 2749 commit 纯本地单副本。）
2. **运维通道**：封仓后直接诊断能力归零是**设计意图**，但须留一条白名单通道（`sudo -u <uid> git … log/status/show`）。**白名单必须只读——一旦放开写命令，封仓即失效。**

**怎么算违宪。** 无可用备份就执行封仓，算。往运维白名单里加任何写命令，算。

## 第五条　机检是本条的兑现面

`agent-shell/bin/katana-check-governed-commits`：三域每一笔 commit 的 message 都必须出自 kernel 的固定模板，否则即绕道。

用 commit message 当判据的理由：它是绕道行为**唯一无法伪造的留痕**——写者可以把文件写到任何地方，但只要想让改动落进仓就得 commit，而 kernel 的 message 是机器生成的固定串。

新增 governed op 时必须同步机检的模板表，否则新 op 的合法 commit 会被误报。

**怎么算违宪。** 改机检的模板表来让已知违规静默通过，算。
