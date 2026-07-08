# claude-memory

Claude Code plugin：operational memory system。

## 机制

- **数据存储**：memory cards 保存在 `KATANA_MEMORY_DIR` 指向的独立 git repo（默认 `/data/memory`），按多租户目录组织（`<KATANA_MEMORY_DIR>/<tenant>/`）
- **服务**：`katana-memory-mcp` 运行在 `:5604`（FastMCP），统一暴露 5 个 MCP tool（`memory_index` / `memory_get` / `memory_create` / `memory_update` / `memory_delete`）
- **SessionStart hook** 是一个带降级的 curl——向服务 `GET /t/<tenant>/index` 拉取 `<memory-index>` hook JSON，服务不可达时注入降级提示，退出码始终为 0
- **所有读写走 MCP tools（id 寻址）**：skills 不直接操作文件系统；数据访问 100% 收敛到服务端
- **L2 正文**不注入到 session context，需要时用 `memory_get(id)` 读取具体 card

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KATANA_MEMORY_MCP_URL` | `http://127.0.0.1:5604` | MCP 服务地址 |
| `KATANA_MEMORY_TENANT` | `uther` | 租户标识，决定 card 存储子目录 |
| `KATANA_MEMORY_DIR` | `/data/memory` | 独立 git repo 根目录 |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `5604` | 服务监听端口 |

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
- 正文必含 `## How to Verify` 段（可执行命令或可核对的 SSoT 路径），供 `memory:validate` 核验事实是否仍成立
- hook 只注入 `status: active`（或缺省）的 card；`stale` / `deprecated` 不注入
- canonical 模板见 `skills/remember/SKILL.md`

## Skills

- `memory:remember` — 通过 `memory_create` / `memory_update` 创建/更新 card（How to Verify 为必填段）
- `memory:validate` — 通过 `memory_index` + `memory_get` 核验 card 健康与事实正确性：L1 结构 + L2 命令核验（默认），L3 SSoT 深度重核（用户要求深度时）；发现矛盾报告 + 给修正建议，不自动改写

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
# 启动服务（开发模式）
PYTHON=~/.cache/katana-mcp/venv/bin/python bash mcp/run-tests.sh

# hook 降级路径回归测试（服务不可达时 fallback JSON 验证）
bash plugins/memory/tests/session-start.test.sh

# 验证 hook 降级输出（手动）
KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 bash plugins/memory/hooks/session-start
```
