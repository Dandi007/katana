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

## 读取优先级

```
当前用户消息 > golden-order.md > goal.md / spec.md > plan.md > progress.md > agent 历史
```

## 默认路径

```
docs/work-records/YYYY/MM/DD/<topic-slug>/
```

项目可在自己的 CLAUDE.md / AGENTS.md 中声明覆盖默认路径；用户给定路径始终优先。
