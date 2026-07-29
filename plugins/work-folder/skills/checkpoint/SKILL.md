---
name: checkpoint
description: 在 context 满或需要切换 session 前，把当前工作状态保存到本地 work folder，确保记忆不丢。当用户想要保存进度、存档当前工作、准备 /clear 或开新 session 时使用。也支持 resume 模式：加载已有 work folder 的状态到新 session，验证环境是否漂移，让新 session 无缝接续。
---

# Checkpoint — Session ↔ Work Folder 搬运

两个模式：

- Save（默认）：session 结束前，把关键记忆持久化到 Work Folder
- Resume：新 session 开始时，加载状态、验证环境并继续工作

## MCP 寻址与持久化契约

1. `folder_id` 是 opaque token（例如 `wf-a1b2c3`）。只从 `wf_create`、`wf_search`、`wf_list` 或 `wf_resume` 的返回值取得并原样传递；不得根据日期、标题、slug 或目录猜测，也不得拼接、解析成物理路径。
2. 文件寻址只使用 `folder_id` + folder-relative `filename`，例如 `folder_id=wf-a1b2c3, filename=progress.md`。绝不向 MCP 传绝对路径、逻辑目录或 `<folder>/<file>` locator。
3. 生命周期只用 `wf_create` / `wf_search` / `wf_list` / `wf_resume` / `wf_save`；文件发现和读写只用 `fs_stat` / `fs_list` / `fs_read` / `fs_create` / `fs_write` / `fs_edit`。需要发现文件时先 `fs_list`，再按返回的 `filename` 精确读取。
4. `fs_create` 只创建不存在的普通文件；目标已存在会失败。`fs_write` 只覆盖已存在文件，绝不隐式 create；局部修改已存在文件用 `fs_edit`。生命周期管理的 identity/control 文件优先交给 `wf_create` / `wf_save` / `wf_resume`，不要自行创建。
5. 每个 mutation 都由 MCP server 经 CAS、policy、manifest 和 Git commit 持久化。client 不对 Work Folder 数据运行原生 Git 命令，也不另做 commit；以 tool 返回的 `git.committed=true`、`git.detail`、`mutation_id`（file tool 还会返回 `commit`）为持久化证据。

## 核心原则

1. **checkpoint 是搬运层，不是定义层**——artifact 的语义、格式和读取优先级见 `references/artifact-formats.md`；Save 负责把 session 状态交给 MCP，Resume 负责把 MCP 状态加载回 session。
2. **协同已有 artifact**——spec.md / plan.md / goal.md 只读引用，不覆盖、不补建；只更新 checkpoint 职责内的状态。
3. **Resume 必须验证**——以 `wf_resume` 返回的 server-side 环境验证为准；BROKEN 必须停止。

---

# Save 模式

## Step 1: 确定 `folder_id`

1. 回顾当前 session，识别工作主题。
2. 上下文已有可信 `folder_id` 时原样复用。
3. 不确定时调用 `wf_search`；若只需最近 active 候选可调用 `wf_list`。
4. 没有合适候选时调用 `wf_create(topic=...)`，使用返回的 `folder_id`。若是否新建会改变用户意图，先询问用户。

不要在 client 文件系统中搜索或验证 Work Folder，也不要接受物理路径作为替代 identity。

## Step 2: 读取当前状态并整理 payload

先载入 `references/artifact-formats.md`。需要现有内容时，以同一个 opaque `folder_id` 调用 `fs_read(filename=...)`：

| Artifact | Save 操作 |
|----------|-----------|
| `_brief.md` | MCP lifecycle 自动维护 identity、updated/status 与 INDEX；checkpoint 不手写 |
| spec.md / plan.md / goal.md | 只读引用；存在时用 `fs_read`，缺失时跳过 |
| progress.md | `wf_create` 已 seed。需要更新 Current/Next/Blocked 时先 `fs_read`，再对已存在文件用 `fs_write` 或 `fs_edit`；checkpoint changelog 由 `wf_save` 追加 |
| golden-order.md | 收集本次尚未落盘的用户拍板/纠正/选择，作为 `golden_order_additions` 传给 `wf_save` |
| findings.md | 有可复用发现时整理为 `findings_addition` 传给 `wf_save` |
| context.md | 整理完整快照，作为 `context_snapshot` 传给 `wf_save` |
| CLAUDE.md / AGENTS.md | `wf_save` 根据 `resume_fields` 生成；checkpoint 不直接创建或覆盖 |

如果确实需要创建一个 lifecycle 之外的普通补充文件：

1. 用 `fs_stat(folder_id, filename)` 确认不存在。
2. 不存在时用 `fs_create`。
3. 已存在时用 `fs_write`（整篇覆盖）或 `fs_edit`（精确局部修改）。
4. `fs_write` 返回 `RESOURCE_NOT_FOUND` 时改用 `fs_create`，不得假设 write 已落盘。

## Step 3: 调用 `wf_save`

调用一次 `wf_save`，至少传：

- `folder_id`
- 能准确描述本次状态的 `summary`
- 完整 `context_snapshot`
- Goal / Phase / Status / Key Context 等 `resume_fields`
- 有内容时的 `golden_order_additions`、`findings_addition`
- 可用时传 `expected_base_sha`；为同一逻辑请求生成并稳定复用 `idempotency_key`

`wf_save` 负责以一个受治理 mutation 追加 progress changelog、写入 context、维护 additions、重生成 Resume Guide、刷新 brief/INDEX 并 Git commit。若 tool 返回错误，不得宣称 checkpoint 完成；报告 error code、是否可重试和当前 commit。

## Step 4: 输出 Checkpoint 摘要

```text
[Checkpoint 完成]
Work folder ID: <folder_id>
更新文件: <tool 返回的 written filenames>
Commit: <tool 返回的 git.detail（或 commit）>
Mutation: <tool 返回的 mutation_id>

恢复方式：
  1. claude --resume <session-id>（如果知道）
  2. 或在新 session 中用 wf_resume(folder_id=<folder_id>) 恢复
```

---

# Resume 模式

## Step R1: 确定 `folder_id`

1. 用户给了 `folder_id` → 原样调用 `wf_resume`。
2. 用户没给 → 用 `wf_search` 或 `wf_list` 查候选，最多列 3 个 `folder_id` + title/goal 让用户选择。
3. 不接受路径、slug 或标题充当 `folder_id`。

## Step R2: 调用 `wf_resume`

以 opaque `folder_id` 调用 `wf_resume`。优先使用返回的 `loaded`、`verification`、`blocked`、`resume_report` 和 `contract`；该调用已追加 resume changelog、刷新 brief/INDEX 并由 server Git commit，不要再重复追加记录。

## Step R3: 补读 Artifact

只有返回的恢复上下文不足时，才按优先级用 `fs_read(folder_id, filename)` 精确补读：

| 顺序 | filename | 目的 |
|------|----------|------|
| 1 | `CLAUDE.md` / `AGENTS.md` | Goal / Status / Resume Steps |
| 2 | `progress.md` | Completed / Current / Next / Blocked |
| 3 | `context.md` | 关键路径、分支、环境 |
| 4 | `findings.md` | 决策、经验、已知问题 |
| 5 | `golden-order.md` | 用户拍板、纠正、选择 |
| 6 | `spec.md` | 设计目标和验收标准（若存在） |
| 7 | `plan.md` | 执行计划和阶段（若存在） |

`RESOURCE_NOT_FOUND` 表示该可选 artifact 不存在，可跳过；其它错误按 tool envelope 处理。不要用 glob 或原生文件工具补读。

## Step R4: 遵守验证结论

| 级别 | 含义 | 动作 |
|------|------|------|
| ✅ MATCH | 环境与存档一致 | 直接从 Current/Next 接续 |
| ⚠️ DRIFT | 环境变化但不阻塞 | 报告差异；需要时通过 `wf_save(context_snapshot=...)` 更新快照后继续 |
| ❌ BROKEN | 关键依赖不可用 | 只输出阻塞报告并等待用户决策 |

不允许用 client-side 猜测覆盖 `wf_resume` 的 server verdict。

## Step R5: 输出 Resume 报告并继续

```text
[Resume 完成]
Work folder ID: <folder_id>
Commit: <tool 返回的 git.detail（或 commit）>

目标: <Goal>
状态: <Phase> / <Status>
环境验证: <MATCH / DRIFT / BROKEN + 明细>
当前任务: <Current>
下一步: <Next>
阻塞项: <Blocked 或“无”>
关键经验: <最近要点，最多 3 条>
```

MATCH / DRIFT 时直接从 Current/Next 继续，不再问“你想做什么”。BROKEN 时不得执行 Current/Next。

---

# 共享约束

- **MCP-only**：Work Folder 的 identity、发现、读写和生命周期操作全部经 work-folder MCP。
- **opaque ID**：始终分开传 `folder_id` 与 `filename`；不得形成或泄露 client locator。
- **Create ≠ Write**：新普通文件 `fs_create`，已有文件 `fs_write` / `fs_edit`；`fs_write` 不会创建。
- **Server Git persistence**：mutation 成功即由 server Git commit；client 不提交 Work Folder 数据。
- **不做 `/clear`**：checkpoint 只存档，不执行 session 切换。
- **不越权**：不创建或修改 spec.md / plan.md。
- **错误不可伪装成功**：以 lifecycle success 字段或 file tool 的 `ok=true`、error envelope、`git.detail` / `commit`、`mutation_id` 为准。
- **BROKEN 必停**：只报告阻塞并等待用户决策。
