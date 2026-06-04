# claude-memory: Rust 重写设计

## §1 第一性原理：要什么

claude-memory 插件的 SessionStart hook 需要在每次 session 启动时扫描 memory card 文件，提取 frontmatter 元数据，注入到 conversation context。

当前实现是 bash + inline Python (~60 行)，存在以下已确认的 bug 和架构问题：
- regex 不限定 frontmatter 边界，会匹配正文中的 `description:` 行
- `read(512)` 截断导致长 frontmatter 的 description 丢失
- 不过滤 `status: deprecated/stale` 的 card
- `metadata.type` 字段未被利用
- 依赖 python3 运行时
- 无容错、无统计

目标：用 Rust 重写核心 scan 逻辑，修复所有已知问题，加 E2E 测试覆盖。

## §2 方案设计

### Binary: `claude-memory-scan`

**输入**：

```
claude-memory-scan --system <path> --project <path>
```

两个参数都可选。不传时不扫描该层级。

**处理流程**：

1. 遍历指定目录下的 `*.md` 文件（非递归，仅顶层）
2. 提取 YAML frontmatter（第一个 `---` 到第二个 `---` 之间）
3. 用 `serde_yaml` 解析，提取字段：
   - `name: String`（必须）
   - `description: String`（必须）
   - `status: String`（可选，缺失视为 `active`）
   - `metadata.type: String`（可选）
4. 过滤：只保留 `status == "active"` 或 status 缺失的 card
5. 格式化为 `- {name} — {description}` 行
6. 按 `metadata.type` 分组（如果有 type 的 card，按 type 归组；无 type 的不分组）

**输出**（stdout）：

合法的 Claude Code hook JSON：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<memory-index>\n## System\n...\n## Project\n...\n\nTotal: 25 cards (2 system + 23 project)\nSystem memory dir: /path/to/system\nProject memory dir: /path/to/project\n</memory-index>\n\n..."
  }
}
```

如果两个目录都没有 active card → exit 0 无输出。

**错误处理**（stderr）：

- 目录不存在 → 跳过（静默，正常场景）
- `.md` 文件无 frontmatter → `warn: {path}: no YAML frontmatter, skipping`
- YAML 解析失败 → `warn: {path}: failed to parse frontmatter: {error}, skipping`
- 缺少 `name` 或 `description` → `warn: {path}: missing required field '{field}', skipping`

**Rust 依赖**：

- `serde` + `serde_derive`
- `serde_yaml`
- `serde_json`
- `tempfile`（dev-only，测试用）

不使用 `clap`——参数简单，手写 `std::env::args()` 解析。

### Build-on-first-run

`hooks/session-start` 保留为 bash wrapper（~15 行）：

```bash
#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${PLUGIN_DIR}/target/release/claude-memory-scan"

if [ ! -x "$BIN" ]; then
  cargo build --release --manifest-path "${PLUGIN_DIR}/Cargo.toml" >&2
fi

SYSTEM_MEMORY="${CLAUDE_MEMORY_SYSTEM_DIR:-${HOME}/.claude/memory}"
PROJECT_MEMORY="${CLAUDE_MEMORY_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}/memory}"

exec "$BIN" --system "$SYSTEM_MEMORY" --project "$PROJECT_MEMORY"
```

- `cargo build` 输出到 stderr，不污染 hook JSON stdout
- binary 缓存在 `target/release/`（gitignored），一次编译长期复用
- 首次编译约 10-15s，之后 binary 启动 <5ms

### hooks.json 简化

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|clear|compact",
      "hooks": [{
        "type": "command",
        "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/session-start\"",
        "async": false
      }]
    }]
  }
}
```

直接调 `session-start`，删除 `run-hook` 中间层。

### 项目结构

```
claude-memory/
├── Cargo.toml
├── src/
│   └── main.rs
├── tests/
│   └── e2e.rs
├── .gitignore                 # target/
├── .claude-plugin/plugin.json
├── CLAUDE.md
├── hooks/
│   ├── hooks.json
│   └── session-start
├── skills/
│   ├── remember/SKILL.md
│   └── validate/SKILL.md
```

删除：`hooks/run-hook`、`templates/card.md`

## §3 改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `Cargo.toml` | 新建 | 项目配置，binary name = `claude-memory-scan` |
| `src/main.rs` | 新建 | Rust 核心逻辑 ~200 行 |
| `tests/e2e.rs` | 新建 | 16 个 E2E 测试 |
| `.gitignore` | 新建 | `target/` |
| `hooks/session-start` | 重写 | bash wrapper with build-on-first-run |
| `hooks/hooks.json` | 修改 | 删除 run-hook 中间层 |
| `hooks/run-hook` | 删除 | 不再需要 |
| `templates/card.md` | 删除 | 与实际 convention 不一致，skill 中已有描述 |
| `skills/remember/SKILL.md` | 修改 | 加 `metadata.type` 引导 |
| `skills/validate/SKILL.md` | 修改 | 加"不要更新 MEMORY.md"约束 |
| `CLAUDE.md` | 修改 | 更新开发/测试说明 |
| `.claude-plugin/plugin.json` | 不变 | — |

## §4 测试计划

全部使用 Rust `#[test]` + `tempfile` crate，`cargo test` 运行。

| 测试名 | 覆盖场景 | 断言 |
|--------|---------|------|
| `empty_dirs` | 空目录 | exit 0，stdout 为空 |
| `system_only` | 只有 system cards | 输出仅含 System section |
| `project_only` | 只有 project cards | 输出仅含 Project section |
| `both_dirs` | 双层都有 card | System + Project 双 section |
| `filter_deprecated` | deprecated 不注入 | deprecated card 不在输出中 |
| `filter_stale` | stale 不注入 | stale card 不在输出中 |
| `no_status_defaults_active` | 缺 status 字段 | 视为 active，出现在输出中 |
| `malformed_frontmatter` | 坏 YAML | stderr 有警告，正常 card 仍输出 |
| `description_in_body` | 正文含 `description:` | 不被错误匹配 |
| `metadata_type_grouping` | 有 type 的 card | 按 type 分组显示 |
| `name_from_yaml` | name 与文件名不同 | 输出用 YAML name |
| `stats_footer` | 多张 card | 末尾有 `Total: N cards` |
| `json_structure` | 输出格式 | 合法 JSON，含 hookSpecificOutput |
| `utf8_chinese` | 中文 description | 正确输出中文 |
| `dir_not_exist` | 不存在的目录 | 跳过，不报错 |
| `non_md_files_ignored` | 混合文件类型 | .txt/.yaml 被忽略 |

## §5 非目标

- 不加 subcommand（validate / stats 等）
- 不做子目录递归扫描
- 不做 card 内容搜索或全文索引
- 不做跨平台 CI（当前只需 macOS ARM）
- 不改 card frontmatter schema（保持向后兼容）
