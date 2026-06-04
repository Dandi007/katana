---
name: validate
description: 核验 memory cards 是否仍与事实(SSoT)一致——结构体检 + 命令核验 + 深度 SSoT 重核，报告矛盾并给修正建议。
---

# memory:validate

核验 memory cards 的**健康状态**与**事实正确性**。

核心立场：card 的价值在于"事实正确"，不在于"新"。一张刚写的 card 也可能已经写错，而它仍以 `status: active` 注入每个 session 的上下文——这是主动的危害。本 skill 以 **SSoT（代码 / 官方文档 / 实际运行状态）为准**，核验 card 内容是否仍然成立。

## 触发

- 用户说"验证 memory"、"检查 facts"、"memory 健康检查"、"core 这些 card 还对不对"
- 用户说"深度扫描 / 深度核验 / 仔细查一遍" → 启用 **L3 深度模式**
- 定期维护、怀疑某些 card 过期时

## 存储路径

路径可通过环境变量覆盖：

| Level | 默认路径 | 环境变量 |
|-------|---------|---------|
| Project | `<project-root>/memory/` | `CLAUDE_MEMORY_PROJECT_DIR` |
| System | `~/.claude/memory/` | `CLAUDE_MEMORY_SYSTEM_DIR` |

## 分层核验（深度由用户输入决定）

| 层 | 何时跑 | 范围 | 做什么 |
|----|--------|------|--------|
| **L1 结构** | 默认 | 全部 card | frontmatter 完整性 + `last_verified` 时间 staleness |
| **L2 命令核验** | 默认 | 有 `How to Verify` 段的 card | 执行其中的命令，按输出判定 |
| **L3 SSoT 重核** | 用户要"深度扫描"时 | 全部 card（含无 How to Verify 的） | 读代码/官方文档/实际状态重新推导事实，与 card 正文 diff 找矛盾 |

- **默认模式** = L1 + L2。
- **深度模式** = L1 + L2 + L3，用户明确要求深度/仔细核验时启用。
- 没有 `How to Verify` 段的 card：默认模式下报为 `unverifiable`（无法跑命令）；深度模式下走 L3，**根据现状用你自己的认知判断**（读相关代码/文档/路径，判断正文是否还成立）。

## 流程

1. **确定 scope**
   - 默认验证当前 project-level memory 目录
   - 用户指定 `--system` 时验证 system-level
   - 用户指定 `--all` 时两层都验证

2. **确定深度**
   - 用户提到"深度/仔细/彻底/逐条核实" → 深度模式（含 L3）
   - 否则默认模式（L1 + L2）

3. **扫描所有 card 文件**
   - 遍历 `<memory-dir>/*.md`，解析 YAML frontmatter

4. **L1 结构检查**
   - 必须有: name, description, status, last_verified → 缺失记为 `incomplete`
   - `last_verified` 超过 30 天 → 记 `stale (time)`（仅提示，不等于错误）
   - `status: deprecated` → 报告但不动

5. **L2 命令核验**（对有 `How to Verify` 段的 card）
   - 执行该段命令，按输出判定：成立 / 矛盾 / 跑不动
   - 命令本身失效（路径/工具已不存在）也视为信号，记入报告

6. **L3 SSoT 重核**（深度模式）
   - 对每张 card：定位其 SSoT（card 里引用的代码路径、`# References`、官方文档），重新推导事实
   - 与 card 正文逐条 diff，找出**矛盾点**（正文称 X，SSoT 实为 Y）
   - 无 How to Verify 的 card 同样在此处理：靠读现状 + 自身认知判断

7. **裁决 → 动作（统一为"报告 + 建议"，不自动改写）**

   | 裁决 | 含义 | 动作 |
   |------|------|------|
   | `verified` | 与 SSoT 一致 | **建议**把 `last_verified` 更新为今天（列入报告，不自动改） |
   | `contradicted` | 与 SSoT 矛盾 | 报告矛盾点 + 给**修正建议 / 建议 patch**；不自动改正文、不自动改 status |
   | `unverifiable` | 无 SSoT / 太主观 | 报告为待人工判断，不动 |
   | `stale (time)` | 仅超 30 天未核验 | 提示，建议安排核验 |
   | `incomplete` | frontmatter 缺字段 | 报告缺哪些字段 |

   **硬约束：本 skill 默认不修改任何 card 文件。** 发现问题只报告 + 建议；是否按建议改写 / 降级 status，交用户确认后再做（或用户显式说"顺手改掉"时才改）。

8. **输出报告**（见下）

**不要更新 MEMORY.md 或任何 INDEX 文件。** SessionStart hook 从 frontmatter 动态扫描生成注入内容，不依赖索引。

> 提示：把 card 标成 `status: stale`/`deprecated` 会让它**立即从 session 注入中消失**（hook 只注入 active）。这是止血手段，但本 skill 不自动执行——交用户决定。

## 报告格式

```
## Memory Validate Report (mode: default | deep, scope: project | system | all)

| 裁决 | 数量 |
|------|------|
| verified     | N |
| contradicted | N |
| unverifiable | N |
| stale (time) | N |
| incomplete   | N |

### ⚠️ Contradicted（与 SSoT 矛盾，建议修正）
- <card-name>: 正文称「X」；SSoT(<source/path>) 实为「Y」
  - 建议: <怎么改 / patch>

### Verified（建议刷新 last_verified → 今天）
- <card-name> (last_verified: YYYY-MM-DD)

### Unverifiable（无核验手段，待人工）
- <card-name>: <原因>

### Stale (time) — last_verified > 30 天
- <card-name> (last verified: YYYY-MM-DD, N days ago)

### Incomplete（frontmatter 缺字段）
- <card-name>: missing [field1, field2]
```
