# claude-memory

Claude Code plugin：operational memory system。

## 机制

- **SessionStart hook** 用 Rust binary (`claude-memory-scan`) 动态扫描所有 memory card 的 YAML frontmatter，注入到 context
- **L2 正文**不注入，需要时用 Read 工具读取具体 card 文件
- 无 INDEX 文件——frontmatter 即唯一数据源，零漂移
- **build-on-first-run**：首次运行自动 `cargo build --release`，之后 binary 缓存在 `target/release/`

## 存储路径

| Level | 默认路径 | 环境变量覆盖 |
|-------|---------|-------------|
| Project | `<project-root>/memory/` | `CLAUDE_MEMORY_PROJECT_DIR` |
| System | `~/.claude/memory/` | `CLAUDE_MEMORY_SYSTEM_DIR` |

Hook 自动合并两层注入。

## Card 格式

```yaml
---
name: kebab-case-slug
description: one-liner（即 L1，注入到 context）
status: active | stale | deprecated
last_verified: YYYY-MM-DD
metadata:
  type: user | feedback | project | reference  # 可选
---
```

正文必含 `## How to Verify` 段（可执行命令或可核对的 SSoT 路径），供 `memory:validate` 核验事实是否仍成立。canonical 模板见 `skills/remember/SKILL.md`。

注：hook 只注入 `status: active`（或缺省）的 card；`stale` / `deprecated` 不注入。

## Skills

- `memory:remember` — 创建/更新 card（How to Verify 为必填段）
- `memory:validate` — 核验 card 健康与事实正确性：L1 结构 + L2 命令核验（默认），L3 SSoT 深度重核（用户要求深度时）；发现矛盾报告 + 给修正建议，不自动改写

## 开发

```bash
# 构建 release binary
cargo build --release

# 运行全部测试（含 16 个 E2E）
cargo test

# 单独测试 hook（模拟真实调用）
CLAUDE_PROJECT_DIR=/path/to/project bash hooks/session-start

# 自定义路径测试
CLAUDE_MEMORY_PROJECT_DIR=/custom/path bash hooks/session-start
```
