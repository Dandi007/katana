# claude-memory

Claude Code plugin：operational memory system。

## 机制

- **数据存储**：memory cards 保存在 `KATANA_MEMORY_DIR` 指向的独立 git repo（默认 `/data/memory`），按多租户目录组织（`<KATANA_MEMORY_DIR>/<tenant>/`）
- **服务**：`katana-memory-mcp` 运行在 `:5605`（FastMCP），统一暴露 5 个 MCP tool（`memory_index` / `memory_get` / `memory_create` / `memory_update` / `memory_delete`）
- **SessionStart hook** 是一个带降级的 curl——向服务 `GET /t/<tenant>/index` 拉取 `<memory-index>` hook JSON，服务不可达时注入降级提示，退出码始终为 0
- **多 runtime 注入**：服务另提供 `GET /t/<tenant>/index.md`（纯文本 `<memory-index>`），供非 Claude runtime 消费；kimi-code 与 OpenCode 的注入客户端在 `runtimes/`（见 `runtimes/README.md`），安装走 `runtimes/install.sh`
- **所有读写走 MCP tools（id 寻址）**：client 不直接操作文件系统；数据访问 100% 收敛到服务端
- **L2 正文**不注入到 session context，需要时用 `memory_get(id)` 读取具体 card

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KATANA_MEMORY_MCP_URL` | `http://127.0.0.1:5605` | MCP 服务地址 |
| `KATANA_MEMORY_TENANT` | `uther` | 租户标识，决定 card 存储子目录 |
| `KATANA_MEMORY_DIR` | `/data/memory` | 独立 git repo 根目录 |
| `KATANA_MEMORY_MCP_HOST` | `127.0.0.1` | 服务监听地址 |
| `KATANA_MEMORY_MCP_PORT` | `5605` | 服务监听端口 |

## Card 格式

```yaml
---
id: <服务生成的唯一 id>
name: kebab-case-slug
description: one-liner（即 L1，注入到 session context）
status: active | stale | deprecated
last_verified: YYYY-MM-DD
metadata:
  type: user | feedback | project | reference  # 可选
---
```

- `id`：由服务端生成，skills 通过 id 寻址（`memory_get` / `memory_update` / `memory_delete`）
- 正文必含 `## How to Verify` 段（可执行命令或可核对的 SSoT 路径），供核验事实是否仍成立
- hook 只注入 `status: active`（或缺省）的 card；`stale` / `deprecated` 不注入

## 使用方式（无 skill，MCP 直用）

> 2026-07-24 起 `memory:remember` / `memory:validate` skill 退役——写卡与核验契约由 MCP server instructions 直接承载，agent 直用 `memory_*` tools：
>
> - **写卡**：先 `memory_index` 查重；新建 `memory_create` / 整字段更新 `memory_update` / 局部改 `memory_edit`（先 `memory_read` 拿精确文本）；正文必含 `## How to Verify`（服务端强校验）
> - **核验**：`memory_index` 看 L1 → `memory_get(id)` 取卡 → 执行卡内 How to Verify 命令核对；发现矛盾更新卡或降 status

## MCP Tools（服务暴露）

| tool | 签名 | 说明 |
|------|------|------|
| `memory_index` | `()` | 返回当前租户全量 card 列表（id / name / description / status / last_verified） |
| `memory_get` | `(id)` | 读取指定 card 全文（含 frontmatter + body） |
| `memory_create` | `(name, description, body, type?)` | 新建 card，服务生成 id，写入 git repo |
| `memory_update` | `(id, description?, body?, last_verified?, status?)` | 更新 card 指定字段 |
| `memory_delete` | `(id)` | 删除 card（git rm + commit） |

## 开发

```bash
# 运行 mcp 测试套件
PYTHON=~/.cache/katana-mcp/venv/bin/python bash mcp/run-tests.sh

# 本地前台起服务
~/.cache/katana-mcp/venv/bin/python -m katana_memory_mcp.server

# hook 降级路径回归测试（服务不可达时 fallback JSON 验证）
bash plugins/memory/tests/session-start.test.sh

# 验证 hook 降级输出（手动）
KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 bash plugins/memory/hooks/session-start
```
