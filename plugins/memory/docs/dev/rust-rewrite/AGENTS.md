# Dev Task: claude-memory Rust 重写

你正在实现一个开发任务。请严格按照以下文档执行。

## 任务文档

| 文档 | 路径 | 说明 |
|------|------|------|
| Spec | `docs/specs/2026-05-26-rust-rewrite-design.md` | 需求、技术方案、约束、验收标准 |
| Plan | `docs/dev/rust-rewrite/plan.md` | 分步实现计划 |

## 工作规则

1. **先读 spec，再读 plan**，确保理解全貌后再动手
2. 严格按 plan.md 的步骤顺序执行
3. 每完成一步，运行该步骤的验证命令，确认通过再继续
4. 不要偏离 spec.md 的范围，不要添加 spec 没有提到的功能
5. 遵守 repo 现有的代码风格和约定
6. 完成后做原子化 commit，每个 commit 聚焦一件事（按 plan 提交策略）
7. 如果 plan 中某步不可行或发现遗漏，在该步骤下方注释说明原因，继续下一步

## 关键文件速查

| 文件 | 职责 |
|------|------|
| `src/main.rs` | **新建** — Rust 核心扫描逻辑 |
| `tests/e2e.rs` | **新建** — 16 个 E2E 测试 |
| `Cargo.toml` | **新建** — Rust 项目配置 |
| `hooks/session-start` | **重写** — bash wrapper + build-on-first-run |
| `hooks/hooks.json` | **修改** — 去掉 run-hook 中间层 |
| `hooks/run-hook` | **删除** — 被直接调用替代 |
| `templates/card.md` | **删除** — 与实际不一致 |
| `skills/remember/SKILL.md` | **修改** — 加 metadata.type 引导 |
| `skills/validate/SKILL.md` | **修改** — 加"不要更新 MEMORY.md"约束 |
| `CLAUDE.md` | **修改** — 更新开发/测试说明 |

## 技术栈

- 语言: Rust (edition 2021)
- 构建: cargo
- 测试: cargo test (integration tests in `tests/e2e.rs`)
- 依赖: serde, serde_yaml, serde_json, tempfile (dev)
- Hook wrapper: bash
- 插件框架: Claude Code plugin system

## 注意事项

- binary name 必须是 `claude-memory-scan`（hooks/session-start 中引用）
- `cargo build --release` 的输出必须在 `target/release/claude-memory-scan`
- E2E 测试用 `env!("CARGO_BIN_EXE_claude-memory-scan")` 定位 binary
- frontmatter 解析必须限定在 `---` 围栏内，不能用 regex 匹配全文
- JSON 输出必须用 `ensure_ascii=false` 等价方式正确处理中文
