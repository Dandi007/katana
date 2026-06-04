# Implementation Plan

## 前置条件

- Rust 工具链已安装（`rustc`, `cargo`）
- 当前在 worktree 分支 `feat/rust-rewrite`
- 已读过 `docs/specs/2026-05-26-rust-rewrite-design.md`

## 实现步骤

### Step 1: 项目脚手架

- **文件**: `Cargo.toml`, `.gitignore`
- **改动**:
  - 创建 `Cargo.toml`，binary name = `claude-memory-scan`
  - 依赖: `serde`, `serde_derive`, `serde_yaml`, `serde_json`
  - dev-dependencies: `tempfile`
  - 创建 `.gitignore`，忽略 `target/`
- **验证**: `cargo check` 通过（需要先创建空 main.rs）

### Step 2: Rust 核心逻辑

- **文件**: `src/main.rs`
- **改动**: 实现完整 scan 逻辑，约 200 行：
  1. CLI 参数解析：`--system <path>` 和 `--project <path>`，手写 `std::env::args()`，不用 clap
  2. frontmatter 提取：读取文件内容，找到第一个 `---` 和第二个 `---` 之间的文本
  3. YAML 解析：用 serde_yaml 反序列化为 struct `CardFrontmatter { name, description, status (Option), metadata (Option<Metadata>) }`，`Metadata { r#type: Option<String> }`
  4. 过滤逻辑：只保留 `status == "active"` 或 status 为 None 的 card
  5. 格式化：`- {name} — {description}`，按 metadata.type 分组（有 type 的按 type 归组，无 type 的不分组直接列出）
  6. 输出 JSON：`{ "hookSpecificOutput": { "hookEventName": "SessionStart", "additionalContext": "..." } }`
  7. stats footer：`Total: N cards (M system + K project)`
  8. 错误处理：目录不存在跳过；无 frontmatter/解析失败/缺必需字段 → stderr 警告，继续处理其他文件
  9. 两个目录都没 active card → exit 0 无输出
- **依赖**: Step 1 完成后
- **验证**: `cargo build --release && echo OK`

### Step 3: E2E 测试

- **文件**: `tests/e2e.rs`
- **改动**: 实现 16 个测试（见 spec §4 测试计划表）。每个测试：
  1. `tempfile::tempdir()` 创建临时目录
  2. 写入 fixture `.md` 文件
  3. `std::process::Command` 调用编译好的 binary
  4. 断言 stdout（JSON 解析后检查）、stderr、exit code
  - 辅助函数：`write_card(dir, filename, frontmatter_yaml, body)` 简化 fixture 创建
  - 辅助函数：`run_scan(system_dir, project_dir) -> Output` 简化 binary 调用
  - binary 路径通过 `env!("CARGO_BIN_EXE_claude-memory-scan")` 获取
- **依赖**: Step 2 完成后
- **验证**: `cargo test` 16 个测试全部通过

### Step 4: hook 集成层

- **文件**: `hooks/session-start`, `hooks/hooks.json`
- **改动**:
  - `hooks/session-start`：重写为 bash wrapper（~15 行），含 build-on-first-run 逻辑。检测 `target/release/claude-memory-scan` 是否存在，不存在则 `cargo build --release` (stderr)，然后 `exec $BIN --system ... --project ...`
  - `hooks/hooks.json`：command 改为直接调 `session-start`，去掉 `run-hook` 中间层
- **验证**: `CLAUDE_PROJECT_DIR=/tmp/test-memory bash hooks/session-start` 能输出 JSON 或静默退出

### Step 5: 删除废弃文件

- **文件**: `hooks/run-hook`, `templates/card.md`
- **改动**: 删除这两个文件
  - `hooks/run-hook`：被 hooks.json 直接调 session-start 替代
  - `templates/card.md`：与实际 convention 不一致，card 格式在 skill 中已有描述
- **验证**: `find . -not -path './.git/*' -type f | sort` 确认文件列表正确

### Step 6: Skill 修复

- **文件**: `skills/remember/SKILL.md`, `skills/validate/SKILL.md`
- **改动**:
  - `remember/SKILL.md`：在"生成 card 内容"步骤中加 `metadata.type` 引导，说明可选值 `user|feedback|project|reference`
  - `validate/SKILL.md`：末尾加"不要更新 MEMORY.md 或任何 INDEX 文件"约束（与 remember 一致）
- **验证**: 人工审阅内容正确

### Step 7: 更新 CLAUDE.md

- **文件**: `CLAUDE.md`
- **改动**: 更新开发和测试说明：
  - 构建：`cargo build --release`
  - 测试：`cargo test`
  - hook 测试方式更新
  - 去掉 python 相关说明
- **验证**: 人工审阅内容正确

## 测试计划

- [x] E2E 测试：16 个场景全覆盖（Step 3）
- [ ] 集成验证：用真实 memory 目录运行 hook（Step 4 验证步骤）
- [ ] 回归验证：新 binary 的输出与旧 python 输出在相同 card 集上语义等价

## 提交策略

| Commit | 范围 | 消息模板 |
|--------|------|----------|
| 1 | Step 1-2 | `feat: rust binary for memory card scanning` |
| 2 | Step 3 | `test: add 16 E2E tests for claude-memory-scan` |
| 3 | Step 4-5 | `refactor: replace python hook with rust binary + build-on-first-run` |
| 4 | Step 6-7 | `fix: update skills and docs for rust rewrite` |
