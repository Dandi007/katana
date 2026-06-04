# Work Folder

持续性工作（跨 session、多阶段、brainstorm→plan→execute）必须使用 work folder。

## 何时创建

任何一条命中即必须**先**创建或绑定 work folder：

- 工作会跨多轮对话 / 跨 session
- 包含 brainstorm → plan → execute 中的两个或以上阶段
- 用户提供了已有 work folder 路径
- 当前目录已存在 `progress.md` / `spec.md` / `plan.md` 等控制面文件

## 每类文件记录什么

| 文件 | 记录内容 |
|------|----------|
| `golden-order.md` | 人类输入与纠正（最高优先级）：用户的选择、答疑、纠正、scope/priority 变更 |
| `goal.md` | 交付目标与验收标准 |
| `spec.md` | 技术设计、约束、范围、非目标 |
| `plan.md` | 任务分解与执行步骤（从已批准的 goal/spec 派生） |
| `progress.md` | 当前阶段、已完成、阻塞、下一步 |
| `findings.md` | 执行中的发现、决策、踩坑、证据 |
| `context.md` | 路径、分支、环境、关键资源快照（用于恢复） |
| `CLAUDE.md` / `AGENTS.md` | Resume Guide：供新 session 快速恢复上下文的摘要 |

## Artifact 格式规范

### golden-order.md

记录用户拍板、纠正、选择的历史。原则：
- **宁多勿漏**——只要是用户拍板/纠正/选择，都要记
- **不要改写或概括掉用户原意**——核心句尽量保留原话
- 已落盘的条目不重复追加

格式：
```markdown
## [YYYY-MM-DD HH:MM] <主题>

- <用户原话或核心意图>（必要时附 why / how to apply）
```

### progress.md

```markdown
# Progress

**Goal:** <从当前 session 上下文总结>
**Status:** <brainstorming / execution / completed>
**Phase:** <当前阶段>
**Updated:** YYYY-MM-DD HH:MM

## Completed
- <已完成事项>

## Current
- <当前进行中>

## Blocked
- None

## Next
- <下一步>

## Changelog
| Time | Action | Detail |
|------|--------|--------|
| HH:MM | checkpoint | <摘要> |
```

### findings.md

```markdown
# Findings

## [YYYY-MM-DD HH:MM] Checkpoint: <session 主题摘要>

### 关键决策
- <本次做的关键决策和排除的方案>

### 可复用经验
- <workaround、踩坑、可复用经验>

### 遇到的问题
- <问题 + root cause + 解法>

### 技术发现
- <非显而易见的技术发现>
```

只记对未来 session 有用的信息，不记流水账。

### context.md

```markdown
# Context

**Updated:** YYYY-MM-DD HH:MM

## 工作上下文
- <当前工作所处的外部状态、依赖条件>

## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| <repo/service/file> | <路径> | <分支> | <说明> |

## 环境信息
- <相关的远程服务器、端口、配置等>
```

实战补充：如果本次工作已经涉及真实交付或部署，应优先写入已合并/待合并的 MR URL、关键主机上的 repo 路径/当前分支/当前 commit、当前实际 cron/job 配置、运行时配置路径与日志路径。

### CLAUDE.md / AGENTS.md (Resume Guide)

供新 session 快速恢复上下文。内容相同，每次覆盖。

```markdown
# Resume Guide

> 由 checkpoint 自动生成。上次更新：YYYY-MM-DD HH:MM

## Goal
<从 spec.md 或 session 上下文提取目标>

## Status
- **Phase:** <当前阶段>
- **Status:** <brainstorming / execution / completed>
- **Work folder:** <work folder 绝对路径>

## Key Context
<从 context.md 提取关键路径和环境信息摘要>

## Key Decisions
<从 findings.md 的"关键决策"section 总结>
<无则写"暂无">

## Known Issues
<从 findings.md 的"遇到的问题"section 总结未解决问题>
<无则写"暂无">

## Lessons
<从 findings.md 的"可复用经验"section 总结>
<无则写"暂无">

## Resume Steps
1. 阅读 progress.md 了解当前进度
2. 阅读 context.md 了解环境状态
3. 如有 spec.md / plan.md，阅读了解设计与计划
4. 继续 progress.md 中 Current/Next 列出的任务
```

## 读取优先级

```
当前用户消息 > golden-order.md > goal.md / spec.md > plan.md > progress.md > agent 历史
```

## 默认路径

```
docs/work-records/YYYY/MM/DD/<topic-slug>/
```

项目可在自己的 CLAUDE.md / AGENTS.md 中声明覆盖默认路径；用户给定路径始终优先。
