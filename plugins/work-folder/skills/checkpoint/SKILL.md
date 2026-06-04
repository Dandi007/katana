---
name: checkpoint
description: 在 context 满或需要切换 session 前，把当前工作状态保存到本地 work folder，确保记忆不丢。当用户想要保存进度、存档当前工作、准备 /clear 或开新 session 时使用。也支持 resume 模式：加载已有 work folder 的状态到新 session，验证环境是否漂移，让新 session 无缝接续。
---

# Checkpoint — 保存与恢复工作状态

两个模式：
- Save（默认）：session 结束前，把关键记忆持久化到 work folder
- Update
- Resume：新 session 开始时，从 work folder 加载状态、验证环境、建立上下文

## 配置

Work folder 路径可通过以下方式覆盖（优先级从高到低）：

| 优先级 | 配置方式 | 示例 |
|--------|---------|------|
| 1 | 环境变量 `KATANA_WORK_FOLDER` | `export KATANA_WORK_FOLDER=智元工作/工作记录` |
| 2 | 项目根目录 `.katana` 文件 | `work_folder_path=智元工作/工作记录` |
| 3 | 默认值 | `docs/work-records` |

如果项目 `.katana` 文件或环境变量指定了路径，以那个为准，忽略默认值。

## 核心原则

1. **对齐 work-folder 约定**——通用 artifact 语义由 work-folder 约定定义（katana work-folder plugin 在 session 开始注入；或项目自身声明的 work folder 约定）；checkpoint 只负责 save/resume，保留/加载现状
2. **协同已有 artifact**——如果 work folder 已有 spec.md / plan.md 等文件，不覆盖、不冲突，只追加/更新
3. **Resume 必须验证**——加载状态后不能盲目继续，必须检查环境是否与存档一致

## 前置检查

执行任何 work folder 写入前，先遵循 work-folder 约定的 artifact contract。

---

# Save 模式

## 流程

### Step 1: 确定 Work Folder

直接判断，不依赖任何外部命令：

1. 回顾当前 session 上下文，识别正在做的工作主题
2. 检查本次 session 中是否已经有在操作的 work folder（看你读写过哪些 `progress.md`、`spec.md`、`plan.md`、`findings.md` 所在的目录）
3. 如果找到了 → 用那个目录作为 work folder
4. 如果没找到 → 问用户：

> 当前 session 未关联 work folder。请选择：
> 1. 提供一个已有路径
> 2. 让我根据当前工作自动创建（路径按 work-folder 约定的默认路径规则）

用户给路径则用那个，选自动则从工作主题推断 `<topic>`，按 rule 规则 `mkdir -p` 创建。

### Step 2: 扫描已有 Artifact

各 artifact 的语义见 work-folder 约定，本步只列 checkpoint 的处理策略：

| Artifact | checkpoint 操作策略 |
|----------|---------------------|
| spec.md / plan.md / goal.md | 只读引用；已有不动，无则不创建（不在 checkpoint 职责内）|
| golden-order.md | **必须维护**——回顾本次 session，把尚未落地的用户输入/纠正/选择/scope·priority 变更追加进去；已有则 append，无则新建 |
| progress.md | **必须更新**——已有则追加 Changelog / 更新 Current·Next；无则新建 |
| findings.md | **本次 session 有内容则更新**——已有追加新 section；无则新建 |
| context.md | **必须更新**——已有则覆盖（快照，不是日志）；无则新建 |

### Step 3: 维护 golden-order.md

`golden-order.md` 是人类输入与纠正的最高优先级存档（语义见 work-folder 约定）。理想状态下 brainstorming 会实时落盘，但 checkpoint 必须做最后兜底：

1. 回顾本次 session 中用户的全部输入：选择、答疑、纠正、scope/priority/方向变更、对方案的明确否决或拍板
2. 对照已有 `golden-order.md`（若存在），找出**尚未记录**的条目
3. 用 Edit 追加（已有文件）或 Write 新建：

```markdown
## [YYYY-MM-DD HH:MM] <主题>

- <用户原话或核心意图>（必要时附 why / how to apply）
```

原则：
- **宁多勿漏**——只要是用户拍板/纠正/选择，都要记
- **不要改写或概括掉用户原意**——核心句尽量保留原话
- 已落盘的条目不重复追加
- 本次 session 确实没有任何用户输入/纠正/选择时（极少见），跳过

### Step 4: 更新 progress.md

**你（LLM）直接读取并编辑**：

- 文件已存在 → 用 Edit 工具更新 Updated 时间、Completed/Current/Next 内容，追加 Changelog 条目
- 文件不存在 → 用 Write 工具新建：

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

### Step 5: 更新 findings.md

**将本次 session 的所有关键记忆统一收进 findings.md**，按 section 分类：

```markdown
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

**操作方式**：
- 文件已存在 → 用 Edit 在文件末尾追加新的 checkpoint section（不覆盖已有内容）
- 文件不存在 → 用 Write 新建，标题为 `# Findings`
- 某个子 section 本次 session 没有内容 → 省略该子 section，不写空占位
- **只记对未来 session 有用的信息，不记流水账**

### Step 6: 更新 context.md

**你（LLM）基于当前 session 上下文填充**，保存环境快照：

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

**操作方式**：
- 文件已存在 → 用 Edit/Write 更新（覆盖，因为 context 是快照而非日志）
- 文件不存在 → 用 Write 新建
- 只记与当前工作直接相关的路径和环境，不列无关信息

**实战补充**：
- 如果本次工作已经涉及真实交付或部署，`context.md` 应优先写入：
  - 已合并/待合并的 MR URL
  - 关键主机上的 repo 路径、当前分支、当前 commit
  - 当前实际 cron / job 配置
  - 运行时配置路径与日志路径
- 当 work folder 初始只有 `spec.md / goal.md / plan.md / progress.md` 时，save 应主动补建 `findings.md`、`context.md`、`CLAUDE.md`、`AGENTS.md`，不要因为“当前还没有”就跳过。

### Step 7: 生成 Resume Guide

在 work folder 中用 Write 工具生成 `CLAUDE.md` 和 `AGENTS.md`（内容相同，每次覆盖）。

**你（LLM）基于当前 session 上下文 + 已有 artifact 填充**：

```markdown
# Resume Guide

> 由 /checkpoint 自动生成。上次更新：YYYY-MM-DD HH:MM

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

### Step 8: 输出 Checkpoint 摘要

```
[Checkpoint 完成]
Work folder: <路径>
更新文件: <列出实际更新/创建了哪些文件>

恢复方式：
  1. claude --resume <session-id>（如果知道）
  2. 或新 session 中阅读 <work-folder>/CLAUDE.md 恢复上下文
```

---

# Resume 模式

从已有 work folder 恢复工作状态到新 session。不是简单地读文件——要**加载、验证、对齐**，确保新 session 能安全接续。

## 流程

### Step R1: 确定 Work Folder

1. 用户给了路径 → 用那个
2. 用户没给 → 在 work-folder 约定的默认路径下按日期倒序找最近、status 非 completed 的 work folder，列出候选（最多 3 个）让用户选择

验证路径存在且至少包含 progress.md 或 CLAUDE.md，否则报错退出。

### Step R2: 加载 Artifact

按优先级顺序读取 work folder 中的所有标准 artifact：

| 顺序 | 文件 | 目的 |
|------|------|------|
| 1 | `CLAUDE.md` / `AGENTS.md` | 快速概览 Goal / Status / Resume Steps |
| 2 | `progress.md` | 了解 Completed / Current / Next / Blocked |
| 3 | `context.md` | 了解关键路径、分支、环境信息 |
| 4 | `findings.md` | 了解关键决策、经验、已知问题 |
| 5 | `spec.md` | 了解设计目标和验收标准（如果存在） |
| 6 | `plan.md` | 了解执行计划和阶段（如果存在） |

文件不存在的直接跳过，不报错。

### Step R3: 验证环境

**这是 resume 的核心步骤——不验证就不能继续。**

基于 context.md 中记录的关键路径和环境信息，逐项检查：

#### 3a. 文件/目录存在性
```
对 context.md 中每个关键路径：
  - 路径是否存在？
  - 如果是 git repo：当前分支是否与记录一致？
  - 是否有未提交的变更？
```

#### 3b. Git 状态
```
对涉及的每个 repo：
  - git status：是否 clean？
  - git log -1：最近 commit 是否与预期一致？
  - 分支是否与 context.md 记录的一致？（如果不一致，可能有人切了分支）
```

#### 3c. 远程服务可达性（如适用）
```
如果 context.md 记录了远程服务器/端口：
  - 简单连通性检查（ping / curl / ssh -o ConnectTimeout=3）
  - 不做深度验证，只确认可达
```

#### 3d. 依赖/工具版本（如适用）
```
如果 context.md 记录了关键工具版本：
  - 检查当前版本是否一致
```

**验证结果分三级**：

| 级别 | 含义 | 动作 |
|------|------|------|
| ✅ MATCH | 环境与存档一致 | 可以直接继续 |
| ⚠️ DRIFT | 环境有变化但不阻塞 | 报告差异，更新 context.md，继续 |
| ❌ BROKEN | 关键依赖不可用 | 报告问题，标记为 Blocked，等用户决策 |

### Step R4: 输出 Resume 报告

向用户输出结构化的恢复报告：

```
[Resume 完成]
Work folder: <路径>
上次 checkpoint: <CLAUDE.md 中的 Updated 时间>

📋 目标: <Goal>
📍 状态: <Phase> / <Status>

🔍 环境验证:
  ✅ <路径/资源> — 一致
  ⚠️ <路径/资源> — 漂移：<描述差异>
  ❌ <路径/资源> — 不可用：<原因>

📌 当前任务:
  - <progress.md 的 Current 内容>

⏭️ 下一步:
  - <progress.md 的 Next 内容>

🚧 阻塞项:
  - <Blocked 内容，或"无">

💡 关键经验（来自上次 session）:
  - <findings.md 中最近一次 checkpoint 的要点，最多 3 条>
```

### Step R5: 更新 progress.md

追加 changelog 记录本次 resume：

```markdown
| HH:MM | resume | 从 checkpoint 恢复；环境验证: N✅ N⚠️ N❌ |
```

如果有 DRIFT 或 BROKEN，同时更新 Blocked section。

### Step R6: 进入工作状态

Resume 完成后，LLM 应该：

1. **已充分了解上下文**——不需要再问"你想做什么"
2. **知道当前在哪个阶段**——直接从 Current/Next 继续
3. **如果有 BROKEN 项**——停止进入工作状态，只输出阻塞报告并等待用户决策

不要等用户再次下达指令。如果环境验证全部 MATCH，直接提出："上次进行到 X，现在继续做 Y，可以吗？"

---

# 共享约束

- **自给自足**：所有步骤 LLM 直接用 Read/Write/Edit/Bash 完成
- **幂等**：Save 多次调用覆盖 CLAUDE.md / AGENTS.md / context.md，追加 progress.md changelog 和 findings.md section
- **不做 git commit**：用户自己决定何时 commit
- **不做 /clear**：checkpoint 只存档，不执行 session 切换
- **Save best-effort**：Save 模式尽量不中断；单个 artifact 更新失败时记录失败原因并继续保存其它 artifact
- **Resume 遇 BROKEN 必须停下**：Resume 模式若 Step R3 出现 ❌ BROKEN，只输出阻塞报告并等待用户决策，不得继续执行 Current/Next
- **不越权**：不创建/修改 spec.md 和 plan.md（那是 brainstorming / writing-plans 的职责）
- **Resume 必须验证**：加载后必须跑 Step R3 验证，不允许跳过直接开工
