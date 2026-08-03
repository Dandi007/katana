# Errors

## 2026-07-13 11:33 — `opencode` CLI 不在非交互 PATH

- 场景：按 `/retrieval:agent-session-search` 核实当前 OpenCode DB path。
- 结果：`opencode db path` 返回 `zsh: command not found: opencode`，但默认 DB `/home/uther/.local/share/opencode/opencode.db` 存在且处于活跃 WAL 状态。
- 处置：本次直接以现存 SQLite 文件取证；后续需要 CLI 时先解析实际 binary 或加载 agent-shell profile，不把 CLI 缺失误判为 session 数据缺失。

## 2026-07-13 11:22 — OpenCode SQL 示例使用过时的 `sessions.summary` schema

- 场景：按 `/retrieval:agent-session-search` 定位 DeepSeek Flash subagent 拒绝的 OpenCode parent/child session。
- 结果：skill 示例查询 `sessions(id, summary)`，当前 OpenCode 1.17.13 DB 实际是单数表 `session`，且会话摘要字段为 `title`，对话内容在 `message.data` / `part.data`。
- 处置：先运行 `.schema session` / `.schema message` / `.schema part`；用 `session.parent_id`还原 Task 父子关系，用 JSON1 解析 `part.data`。本次已据此定位 parent `ses_0a70165afffe4BFtWHZ4qytKih` 和 child `ses_0a6ffbe91ffeYiuB3mI6cj59Jd`。

## 2026-07-11 16:04 — JavaScript template literal 误解析 shell 默认值展开

- 场景：按 `/retrieval:agent-session-search` 探测 `AGENT_SESSION_STORE`、OpenCode SQLite 与 Loop Engine colocated sessions。
- 结果：把 shell 的 `${AGENT_SESSION_STORE:-$HOME/.claude/projects}` 直接写进 JavaScript template literal，JavaScript 将其当作非法插值表达式，整次只读探测未执行。
- 处置：改用不含 JavaScript interpolation 的普通字符串传给 shell；重跑后确认 Claude store 约 33k JSONL、OpenCode DB 与 Loop Engine colocated sessions 均可读。

## 2026-07-11 16:06 — rg 正则与 shell 单引号组合导致 unmatched quote

- 场景：并行统计 Claude sessions 中 wrapper/spec/submit/direct-drain/plugin 相关命中。
- 结果：`directdrain` pattern 同时包含单引号字符并被外层 shell 单引号包裹，zsh 报 `unmatched '`；其余四个查询正常完成，该项无结果。
- 处置：后续把命令提取改为先用 `jq` 还原 tool-use command，再用不含引号元字符的多个固定 pattern 过滤；不再把复合 regex 直接插入 shell 单引号。

## 2026-07-13 18:17 — `rg` 不在非交互 PATH

- 场景：按 `/retrieval:agent-session-search` 统计 Claude Code 中命中 Loop Engine 的顶层 JSONL。
- 结果：`rg` 返回 `zsh: command not found: rg`，只读统计未执行。
- 处置：改用 OpenCode 内置 Grep/Glob，或在需要完整文件清单时使用系统现有 `grep`/`find`；不把工具缺失误判为 session 不存在。

## 2026-07-28 22:10 — session-engine `list_events` 超大页返回非 JSON 错误

- 场景：按 `/retrieval:agent-session-search` 一次请求 Claude Code session 的 2000 条归一化事件，并在 JavaScript 编排层解析 MCP 文本结果。
- 结果：`list_events(limit=2000)` 返回以 `Error exec...` 开头的非 JSON 文本，随后 `JSON.parse` 报 `Unexpected token 'E'`；会话源文件未被修改。
- 处置：不要假设超大 `limit` 必然返回 JSON；改用受控分页，解析前先检查 `isError` / 文本首字符，或直接只读分析对应 JSONL 的末尾记录。

## 2026-07-28 22:13 — jq 在重建 assistant 摘要时丢失原记录上下文

- 场景：从 Claude Code JSONL 过滤 assistant 文本，先把 `.message.content` 映射成数组后又读取 `.timestamp`。
- 结果：管道上下文已经变成 content array，`jq` 连续报 `Cannot index array with string "timestamp"`；没有修改会话源文件。
- 处置：映射 content 前先用 `. as $record` 保存原记录，后续时间戳从 `$record.timestamp` 读取。
