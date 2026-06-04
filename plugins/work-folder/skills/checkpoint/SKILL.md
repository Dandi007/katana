---
name: checkpoint
description: 在 context 满或需要切换 session 前，把当前工作状态保存到本地 work folder，确保记忆不丢。当用户想要保存进度、存档当前工作、准备 /clear 或开新 session 时使用。也支持 resume 模式：加载已有 work folder 的状态到新 session，验证环境是否漂移，让新 session 无缝接续。
---

# Checkpoint — Session ↔ Work Folder 搬运

两个模式：
- Save（默认）：session 结束前，把关键记忆持久化到 work folder
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

1. **checkpoint 是搬运层，不是定义层**——artifact 的语义、格式、优先级由 work-folder 约定定义（SessionStart hook 注入）；checkpoint 只负责把 session 状态 dump 进 work folder、或从 work folder 加载回 session
2. **协同已有 artifact**——如果 work folder 已有 spec.md / plan.md 等文件，不覆盖、不冲突，只追加/更新
3. **Resume 必须验证**——加载状态后不能盲目继续，必须检查环境是否与存档一致

---

# Save 模式

## 流程

### Step 1: 确定 Work Folder

1. 回顾当前 session 上下文，识别正在做的工作主题
2. 检查本次 session 中是否已经有在操作的 work folder（看你读写过哪些 artifact 所在的目录）
3. 如果找到了 → 用那个目录
4. 如果没找到 → 问用户：提供已有路径，或根据当前工作自动创建（路径按 work-folder 约定的默认路径规则）

### Step 2: 按 work-folder 约定 dump 各 artifact

按 work-folder 约定中定义的 artifact 语义和格式，逐个处理：

| Artifact | checkpoint 操作 |
|----------|----------------|
| spec.md / plan.md / goal.md | 只读引用；已有不动，无则不创建（不在 checkpoint 职责内）|
| golden-order.md | **必须维护**——回顾本次 session，把尚未落地的用户输入/纠正/选择追加进去 |
| progress.md | **必须更新**——更新状态、追加 Changelog |
| findings.md | **有内容则更新**——追加本次 session 的关键决策、经验、问题 |
| context.md | **必须更新**——覆盖写入环境快照（快照，不是日志）|
| CLAUDE.md / AGENTS.md | **必须生成**——Resume Guide，供新 session 快速恢复上下文 |

每个 artifact 的具体格式和字段定义，遵循 work-folder 约定（SessionStart hook 注入的 rules）。checkpoint 不重复定义这些格式。

### Step 3: 输出 Checkpoint 摘要

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
| 5 | `golden-order.md` | 了解用户拍板/纠正/选择的历史 |
| 6 | `spec.md` | 了解设计目标和验收标准（如果存在） |
| 7 | `plan.md` | 了解执行计划和阶段（如果存在） |

文件不存在的直接跳过，不报错。

### Step R3: 验证环境

**这是 resume 的核心步骤——不验证就不能继续。**

基于 context.md 中记录的关键路径和环境信息，逐项检查：

- **文件/目录存在性**：context.md 中每个关键路径是否存在；git repo 的分支、未提交变更
- **Git 状态**：是否 clean、最近 commit、分支一致性
- **远程服务可达性**（如适用）：简单连通性检查
- **依赖/工具版本**（如适用）：版本一致性

**验证结果分三级**：

| 级别 | 含义 | 动作 |
|------|------|------|
| ✅ MATCH | 环境与存档一致 | 可以直接继续 |
| ⚠️ DRIFT | 环境有变化但不阻塞 | 报告差异，更新 context.md，继续 |
| ❌ BROKEN | 关键依赖不可用 | 报告问题，标记为 Blocked，等用户决策 |

### Step R4: 输出 Resume 报告

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
