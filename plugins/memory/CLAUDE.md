# claude-memory

Claude Code plugin：operational memory system。

## 机制

- **SessionStart hook** 用纯 shell + awk (`hooks/scan-memory.awk`) 动态扫描所有 memory card 的 YAML frontmatter，注入到 context
- **L2 正文**不注入，需要时用 Read 工具读取具体 card 文件
- 无 INDEX 文件——frontmatter 即唯一数据源，零漂移
- **无构建、无二进制、无下载**：hook 是文本脚本，随包发布即可运行，到处有 `bash` + `awk` 就能跑（不再依赖 Rust binary / cargo / GitHub Release 预编译）

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

### Frontmatter 解析约定（与 scanner 对齐）

scanner 是逐行解析（非完整 YAML parser），覆盖 `memory:remember` 写出的稳定格式：

- `name` / `description` / `status` / `metadata.type` 均为单行 scalar
- plain scalar 中的 ` #`（空格+井号）按 YAML 规则视为行内注释并剥除；需要保留井号请用引号包裹整个值
- 值里若要出现 ASCII `: `（冒号+空格），必须加引号，否则不符合约定

## Skills

- `memory:remember` — 创建/更新 card（How to Verify 为必填段）
- `memory:validate` — 核验 card 健康与事实正确性：L1 结构 + L2 命令核验（默认），L3 SSoT 深度重核（用户要求深度时）；发现矛盾报告 + 给修正建议，不自动改写

## 开发

```bash
# 单独测试 hook（模拟真实调用）
CLAUDE_PROJECT_DIR=/path/to/project bash hooks/session-start

# 自定义路径测试
CLAUDE_MEMORY_PROJECT_DIR=/custom/path bash hooks/session-start

# 字节级回归测试（scanner 输出对齐 golden）
bash tests/scan-memory.test.sh
```
