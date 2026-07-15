---
name: checkpoint
description: 在 context 满或需要切换 session 前，通过 work-folder MCP 保存当前工作状态，确保记忆不丢。当用户想要保存进度、存档当前工作、准备 /clear 或开新 session 时使用。也支持 resume 模式：加载已有 work folder 的状态到新 session，验证环境是否漂移，让新 session 无缝接续。
---

# Checkpoint — Session ↔ Work Folder 搬运

两个模式：
- Save（默认）：session 结束前，把关键记忆持久化到 work folder
- Resume：新 session 开始时，从 work folder 加载状态、验证环境、建立上下文

## MCP 工具

| 目的 | 工具 |
|------|------|
| 新建 / 恢复 / 查找 / 存档 | `wf_create` / `wf_resume` / `wf_search` / `wf_save` |
| 列举工作记录 | `wf_list` |
| 读写 control artifact | work-folder MCP 的 `fs_read` / `fs_write` / `fs_edit` / `fs_glob` |
| 刷新索引 | `wf_reindex` |

Work folder 的物理根由 MCP server 管理。不要解析、展示或用原生文件工具访问其宿主机路径。

## 核心原则

1. **checkpoint 是搬运层，不是定义层**——artifact 的语义、格式、优先级定义见 `references/artifact-formats.md`（本 skill 调用时载入；session 常驻只留一句话锚点）；checkpoint 只负责把 session 状态 dump 进 work folder、或从 work folder 加载回 session
2. **协同已有 artifact**——如果 work folder 已有 spec.md / plan.md 等文件，不覆盖、不冲突，只追加/更新
3. **Resume 必须验证**——加载状态后不能盲目继续，必须检查环境是否与存档一致

---

# Save 模式

## 流程

### Step 1: 确定 Work Folder

1. 回顾当前 session 上下文，识别正在做的工作主题
2. 如果本次 session 已由 `wf_create` / `wf_resume` 返回 active work folder，沿用它
3. 用户给已有记录 → 调 `wf_resume`；只有主题线索 → 调 `wf_search` 后让用户选候选
4. 没有可恢复记录 → 问用户后调 `wf_create`，由 server 分配逻辑路径

### Step 2: 按 work-folder 约定 dump 各 artifact

先用 work-folder MCP 的 `fs_read` 读取已有 control artifact，再按定义逐个处理；创建整文件用 `fs_write`，局部追加/替换用 `fs_edit`：

| Artifact | checkpoint 操作 |
|----------|----------------|
| `_brief.md` | **MCP 自动维护**——`wf_create` seed、`wf_save`/`wf_resume` 刷新 `updated` + 拉回 active。顶层 `INDEX.md` 由 `wf_reindex` 聚合，非单次 checkpoint 职责 |
| spec.md / plan.md / goal.md | 只读引用；已有不动，无则不创建（不在 checkpoint 职责内）|
| golden-order.md | **必须维护**——回顾本次 session，把尚未落地的用户输入/纠正/选择追加进去 |
| progress.md | **必须更新**——更新状态、追加 Changelog |
| findings.md | **有内容则更新**——追加本次 session 的关键决策、经验、问题 |
| context.md | **必须更新**——覆盖写入环境快照（快照，不是日志）|
| CLAUDE.md / AGENTS.md | **必须生成**——Resume Guide，供新 session 快速恢复上下文 |

每个 artifact 的具体格式和字段定义见 `references/artifact-formats.md`（与本 skill 同目录，调用时载入）。checkpoint 不重复定义这些格式。文件不存在时主动创建，不要因为"当前还没有"就跳过。

artifact 更新完成后调用 `wf_save` 执行存档语义；不要绕过 MCP 工具或执行 client-side git 操作。

### Step 3: 输出 Checkpoint 摘要

```
[Checkpoint 完成]
Work folder: <路径>
更新文件: <列出实际更新/创建了哪些文件>

恢复方式：
  1. claude --resume <session-id>（如果知道）
  2. 或新 session 中调用 wf_resume 恢复该 work folder
```

---

# Resume 模式

从已有 work folder 恢复工作状态到新 session。不是简单地读文件——要**加载、验证、对齐**，确保新 session 能安全接续。

## 流程

### Step R1: 确定 Work Folder

1. 用户给了逻辑路径 / id → 调 `wf_resume`
2. 用户没给 → 调 `wf_search`（必要时 `wf_list`），列出最近且 status 非 completed 的候选（最多 3 个）让用户选择，再调 `wf_resume`

由 `wf_resume` 验证记录和 control artifact；不要自行探测 server 文件系统。

### Step R2: 加载 Artifact

按优先级顺序，用 work-folder MCP 的 `fs_read` 读取所有标准 artifact：

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

以 `wf_resume` 返回的环境验证为准，并结合 context.md 中记录的信息逐项解释：

- **文件/目录存在性**：使用 `wf_resume` 的环境检查结果，不直接探测 work-folder 存储
- **Git 状态**：使用 `wf_resume` 返回的 clean/commit/branch 检查结果
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

通过 `fs_edit` 追加 changelog 记录本次 resume；需要同步快照时调用 `wf_save`：

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

- **MCP 单通道**：确定/创建/恢复/存档只用 `wf_*`；work-folder control artifact 只用 work-folder MCP 的 `fs_read` / `fs_write` / `fs_edit` / `fs_glob`
- **幂等**：Save 多次调用覆盖 CLAUDE.md / AGENTS.md / context.md，追加 progress.md changelog 和 findings.md section
- **不绕过 server 做 git commit**：存档语义交给 `wf_save`
- **不做 /clear**：checkpoint 只存档，不执行 session 切换
- **Save best-effort**：Save 模式尽量不中断；单个 artifact 更新失败时记录失败原因并继续保存其它 artifact
- **Resume 遇 BROKEN 必须停下**：Resume 模式若 Step R3 出现 ❌ BROKEN，只输出阻塞报告并等待用户决策，不得继续执行 Current/Next
- **不越权**：不创建/修改 spec.md 和 plan.md（那是 brainstorming / writing-plans 的职责）
- **Resume 必须验证**：加载后必须跑 Step R3 验证，不允许跳过直接开工
