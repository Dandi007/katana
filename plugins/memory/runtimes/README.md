# runtimes/ — 非 Claude runtime 的 memory-index 注入客户端

服务端为不同 runtime 提供两种 index 形态（同一 `render_index` 渲染，SSoT 一致）：

| Endpoint | 格式 | 消费方 |
|---|---|---|
| `GET /t/<tenant>/index` | Claude Code SessionStart hook JSON（`additionalContext` 包裹） | Claude Code（`hooks/session-start`） |
| `GET /t/<tenant>/index.md` | 纯文本 `<memory-index>` | kimi-code、OpenCode 及其他 runtime |

各 runtime 没有统一的 "SessionStart hook" 抽象，按各自最佳实践实现"每 session 注入一次"：

## kimi-code（`kimi-code/user-prompt-hook`）

kimi-code 的 SessionStart hook 是 observation-only（stdout 被丢弃），无法注入 context。
改用 **UserPromptSubmit hook**（stdout 会以 `<hook_result>` 注入，空输出不注入）+
per-session marker 文件（stdin JSON 带 `session_id`）实现每 session 只注入一次；
服务不可达时不落 marker，下个 prompt 自动重试。

配置（`install.sh` 自动追加到 `~/.kimi-code/config.toml`）：

```toml
[[hooks]]
event = "UserPromptSubmit"
command = "<repo>/plugins/memory/runtimes/kimi-code/user-prompt-hook"
timeout = 10
```

## OpenCode（`opencode/katana-memory-index.ts`）

OpenCode 无 shell-command hook，走 **plugin** 的
`experimental.chat.system.transform` hook：把 `<memory-index>` 追加进 system prompt
数组，按 `sessionID` 缓存保证同一 session 内容固定（prompt cache 友好）。
服务不可达时该 session 静默降级。

安装 = symlink 到 plugin 扫描目录（`install.sh` 自动做）：

```
~/.config/opencode/plugins/katana-memory-index.ts -> <repo>/plugins/memory/runtimes/opencode/katana-memory-index.ts
```

## 安装

```bash
plugins/memory/runtimes/install.sh          # 安装全部（检测到哪个 runtime 装哪个）
plugins/memory/runtimes/install.sh kimi-code
plugins/memory/runtimes/install.sh opencode
```

幂等：kimi-code 已有条目则跳过；opencode 为 `ln -sf` 覆盖。
两个客户端都读 `KATANA_MEMORY_MCP_URL` / `KATANA_MEMORY_TENANT` 环境变量（默认
`http://127.0.0.1:5605` / `uther`）。

注意：MCP tools（`memory_*` 5 个）的接入是独立一层，走各 runtime 自己的 MCP 配置
（kimi：`~/.kimi-code/mcp.json`；OpenCode：`~/.config/opencode/opencode.jsonc` 的
`mcp` 段），本目录只负责 index 注入。
