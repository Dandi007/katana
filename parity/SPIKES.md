# SPIKES — katana OpenCode parity

实现前置的三个 spike，结论回填 spec 与 contracts。

## Spike① fpa throw 呈现行为

**问题：** OC `tool.execute.after` handler 中 `throw new Error(stderr)` 是否把错误呈现给模型（与 CC PostToolUse hook exit 2 等效）？

**验证方法：** 在 OC v1.16.2 源码 `/tmp/oc-1.16.2` 中追踪 `tool.execute.after` 的错误处理路径：
- `packages/opencode/src/plugin/index.ts` 中 `tool.execute.after` hook 调用包裹在 try/catch 中
- catch 到的 error 被格式化为 tool output 的一部分，回传给模型作为 tool result
- 模型看到的 tool result 包含 `[plugin error: <message>]` 格式的错误信息

结论：throw 路径在 OC v1.16.2 中**有效**——throw 的 Error message 被 OC 捕获并作为 tool result 的一部分呈现给模型，与 CC PostToolUse hook exit 2 语义等效。adapter 采用 `throw new Error(stderr)` 作为 fpa exit 2 的处理方式，无需降级到 console.error。

## Spike② config hook 突变 skills.paths 时序

**问题：** OC v1.16.2 的 `config` hook（`plugin/index.ts:251`）运行时向 cfg 追加 `skills.paths`，skill discovery 能否在 session 开始前看到这些路径？

**验证方法：** 追踪 OC v1.16.2 启动序列：
- `packages/opencode/src/plugin/index.ts:251` — config hook 在 plugin 初始化阶段被调用
- `packages/opencode/src/skill/index.ts:211` — skill discovery 读取 `cfg.skills.paths` 发生在 plugin 初始化之后
- 时序：plugin load → config hook → skill discovery → session start

结论：config hook 突变 `skills.paths` 在 OC v1.16.2 中**时序可见**——skill discovery 发生在 config hook 之后，追加的路径会被正确扫描。但 config hook 未文档化，存在未来版本变更风险。adapter 首选 config hook 路径，fallback 为 README 手动 config。

## Spike③ npm 包名可用性

**问题：** `opencode-katana` 或 `@dandi007/opencode-katana` 在 npm registry 是否可用？

**验证方法：** `npm view opencode-katana` 和 `npm view @dandi007/opencode-katana` 查询 registry。

结论：`opencode-katana` 在 npm registry **未被占用**（2026-06-08 查询返回 404），可直接使用。备选 `@dandi007/opencode-katana` 同样可用。package.json 采用 `opencode-katana` 作为首选名，publish 时如遇冲突回退到 scoped 名。
