# claude-memory — Agent Instructions

Claude Code plugin：operational memory system。用 SessionStart hook 扫描 memory card frontmatter 并注入 conversation context。

## Repo 概览

| 维度 | 值 |
|------|-----|
| 语言 | Bash + awk（纯脚本，无编译产物） |
| 核心 | `hooks/session-start`（dir 解析 + 调度）+ `hooks/scan-memory.awk`（扫描/格式化/JSON 输出） |
| 测试 | `bash tests/scan-memory.test.sh`（字节级 golden 回归）+ 仓库根 `parity/e2e/run.sh`（双 runtime） |
| 插件框架 | Claude Code plugin system + OpenCode parity adapter |

## 设计要点

- scanner 是**逐行解析**，不是完整 YAML parser；只承诺覆盖 `memory:remember` 写出的稳定 card schema（见 `CLAUDE.md` 的「Frontmatter 解析约定」）。
- 输出格式以 `tests/fixtures/expected.json`（golden，原 Rust `claude-memory-scan` 的冻结输出）为准；任何改动必须保持 `tests/scan-memory.test.sh` 通过。
- 历史：本插件曾用 Rust binary 实现，因二进制分发与 npm 发布模型不匹配（包里漏装二进制 → OpenCode 侧静默失效）于 2026-06-08 改为纯 shell。详见 `docs/specs/2026-06-08-shell-rewrite.md`。
