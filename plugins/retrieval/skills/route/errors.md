# Errors

## 2026-07-10 20:43 — Subagent 在 Search consistency 求真时 stream disconnected

- 场景：为 KB MCP Full VFS 设计比较 synchronous indexing、observable eventual projection、blind eventual 三种 consistency。
- 路由：`/retrieval:route` → `/retrieval:code`，只读分析现役 shared vault-search 与三域 MCP。
- 结果：subagent 返回 `stream disconnected before completion`，没有产出可用结论；本地主 agent 仍可直接读取已知源码证据继续，不构成信息源失败。
- 处置：不重试外部生成流；以本地源码和此前已完成的 capability contract 为 SSoT，结果可信度保持 high。

## 2026-07-11 07:00 — route references 被错误地相对 plugin 根解析

- 场景：为 Loop Engine failure taxonomy 做源码求真，加载 `/retrieval:route` 的 `references/routing.md`、`credibility.md`、`fallback.md`。
- 结果：最初在 plugin 根执行 `sed references/...`，得到 `No such file or directory`；这是调用方路径解析错误，不是 skill 资源缺失。
- 处置：所有 `SKILL.md` 内相对路径必须相对该 `SKILL.md` 所在目录解析，即读取 `skills/route/references/...`。

## 2026-07-29 19:21 — zsh `path` 特殊变量覆盖 PATH，临时文件清理命令被安全策略拒绝

- 场景：经 `/retrieval:route` 只读核验 NUC 上 `douyin-live` 服务的 API 反代链路。
- 结果：首次用 `for path in ...` 探测时，zsh 将特殊数组变量 `path` 同步到 `PATH`，导致循环内 `curl: command not found`；改名后的一次重试又因命令包含 `rm -f /tmp/...` 被执行安全策略拒绝。两次均未执行服务变更，也未影响服务。
- 处置：zsh 脚本不要使用 `path` 作为普通变量，改用业务前缀名（如 `probe_path`）；只需丢弃响应体时直接写 `/dev/null`，不要创建再删除临时文件。

## 2026-07-29 20:26 — route reference 文件名按旧猜测读取失败

- 场景：为 `douyin-live` TS 录制标题透传修复加载 `/retrieval:route` 的 references。
- 结果：误读了不存在的 `routing-table.md`、`credibility-protocol.md`、`fallback-strategies.md`，命令在第一个缺失文件处退出；没有影响服务或代码。
- 处置：以 `SKILL.md` 明示的相对路径为准，实际文件为 `routing.md`、`credibility.md`、`fallback.md`，先用 `rg --files` 核实资源名。
