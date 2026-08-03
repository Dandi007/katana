# OpenCode Master 已知错误

## 2026-07-11 18:45：systemd user jobd 未继承 provider token 时 `opencode run` 静默 `EXIT:1`

### 现象

Loop Engine 由 `systemd --user` 的 `loop-engine-jobd.service` 拉起 OpenCode Worker。`opencode run --format json` 在约 2 秒内 `EXIT:1`，stdout/stderr 为空、token 为 0；OpenCode session 已创建，但没有任何模型输出。Loop Engine 的旧 fleet claim 已把 trigger 从 `open` 改为 `done`，随后 drain 还会错误显示 `reason=drained` / job `succeeded`。

### 根因

- 宿主 OpenCode provider 的 `apiKey` 使用 `{env:LINGZHI_API_KEY}` / `{env:ZHIPU_CODE_API_KEY}` 引用。
- 交互式 shell 有这两个变量，但 user jobd 的 effective environment 只有 `NODE_OPTIONS` 与 `PATH`；daemon → drain → OpenCode child 因此没有 provider token。
- OpenCode DB 中 assistant message 的真实错误是 HTTP 401“未提供令牌”；直接从带凭证的交互式环境运行同一隔离 helper + 同一模型可稳定返回 `OK`，证明模型、endpoint 与 isolation config 本身可用。

### 安全处理

1. 先检查 `systemctl --user show loop-engine-jobd.service -p Environment` 的变量名，不要输出值。
2. 临时恢复可用性可用 `systemctl --user import-environment LINGZHI_API_KEY ZHIPU_CODE_API_KEY` 后 restart jobd；长期应使用权限为 `0600` 的 systemd `EnvironmentFile` 或 credentials 机制，避免依赖交互 shell。
3. restart 后必须从 jobd 上下文分别跑目标 provider 的最小 probe；交互式 `opencode run` 成功不能证明 daemon 环境成功。
4. 旧 seed 必须终止并新建 run/spec/root；不得把 `trigger=done + job=succeeded` 当成 Worker 成功，也不得手工补 PR Store。

### 实证现场

- OpenCode：`1.17.13`
- failed run：`/data/loop-engine/runs/2026-07-11T183910-5949005a`
- job：`job-2026-07-11T183909-d9dac3b4`
- Worker model：`lingzhi/deepseek-v4-pro`

## 2026-07-11：`opencode run --auto` 无法自动回复 subagent permission

### 现象

Loop Engine 的 OpenCode reviewer 以 `write:true` 运行，因此 Adapter 加入 `opencode run --auto`。reviewer prompt 虽只允许 Read/Grep/Glob 和写一个 feedback file，模型仍可见 `task`/`bash`，并创建 general subagent。subagent 访问 workspace 外目录时产生 `external_directory` permission ask，进程长期停在 `ep_poll`，无新日志、verdict 或 feedback。

### 根因

- OpenCode `run --auto` 的 event loop 只在 `permission.sessionID === root sessionID` 时自动回复；subagent 使用独立 session ID，因此其 permission event 被跳过。
- `task` 工具会等待 child session 完成；child 又在等待无人回复的 permission，形成确定性死等。
- prompt 中的 toolbox/write-scope 只是软约束；若 permission rules 未 deny，工具仍对模型可见。

源码锚点：

- `/data/code/third_party/opencode/packages/opencode/src/cli/cmd/run.ts`：`permission.asked` 先按 root `sessionID` 过滤，再执行 `--auto` reply。
- `/data/code/third_party/opencode/packages/opencode/src/tool/task.ts`：subagent 创建独立 session 并等待其完成。
- `/data/code/self/loop-engine/src/adapters/opencode.ts`：`write:true` 时加入 `--auto`。

### 安全处理

1. 对无人值守 reviewer 用 `OPENCODE_PERMISSION` 建硬边界：deny `task`、`bash`、network/search、skill/todo；`edit` 先 deny `*`，再只 allow 精确 feedback path；`external_directory` 先 deny `*`，再 allow staged input root；read/grep/glob allow。
2. 用 `opencode debug agent build` 在隔离环境验证 resolved rule 顺序；不要只检查 JSON 字面量。
3. 已卡住时先保存 DB/log/session/permission id，再 SIGTERM OpenCode child，让上层 Engine 留下真实 `SIGNAL:SIGTERM` journal；确认进程退出、无 verdict 后再恢复 Store，不直接伪造 verdict。
4. 不能只加强 prompt；权限系统才是 hard boundary。

### 实证现场

- run root：`/data/vault/.runtime/loop-engine-model-provider-failover/dev-dispatch/00-dev-dispatch-bootstrap`
- review run：`2026-07-11T102828-85559dc4`
- 卡住的 child permission：`external_directory`，目标 `/data/code/self/loop-engine/src/*`
- 终止后 journal：`error=exec`、`status=SIGNAL:SIGTERM`；候选 workspace 与 source repo 未被修改。

## 2026-07-13：OpenCode 1.17.13 session HTTP route 已迁移到 typed HttpApi 目录

### 现象

为设计 OpenCode Plugin 内的 Dev Dispatch orchestrator，按旧路径读取 `packages/opencode/src/server/routes/session.ts` 时报 `No such file or directory`。

### 根因

当前本地 OpenCode 1.17.13 源码已将 session HTTP API 拆到 typed HttpApi 目录：

- handler：`packages/opencode/src/server/routes/instance/httpapi/handlers/session.ts`
- endpoint/schema group：`packages/opencode/src/server/routes/instance/httpapi/groups/session.ts`

### 处置

涉及当前 session create/prompt/prompt_async 路由的求真，先用 `rg --files packages/opencode/src/server | rg 'session|route'` 定位实际路径；不再假设旧的 `server/routes/session.ts` 存在。
