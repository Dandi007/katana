# claude-memory — Agent Instructions

Claude Code plugin：operational memory system。用 SessionStart hook 扫描 memory card frontmatter 并注入 conversation context。

## Repo 概览

| 维度 | 值 |
|------|-----|
| 语言 | Rust (binary) + Bash (hook wrapper) |
| 构建 | `cargo build --release` |
| 测试 | `cargo test` |
| 插件框架 | Claude Code plugin system |

## Current Dev Task

正在进行 Rust 重写。详细任务文档在 `docs/dev/rust-rewrite/`：

| 文档 | 说明 |
|------|------|
| `docs/specs/2026-05-26-rust-rewrite-design.md` | 设计 spec |
| `docs/dev/rust-rewrite/plan.md` | 分步实现计划 |
| `docs/dev/rust-rewrite/AGENTS.md` | 任务级指引 |

**请先读 `docs/dev/rust-rewrite/AGENTS.md`，然后按 plan 执行。**
