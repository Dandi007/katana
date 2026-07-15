---
name: search-note
description: 未迁本地知识子树的只读检索源；已迁 wiki/work-folder 域分别路由 wiki_search/wf_search。query_lancedb.py --mode auto（vector→keyword 自降级）。
---

# /retrieval:search-note

本地 Markdown 知识库的只读检索源。**任何情况下不写入 KB。**

## 配置（.katana）

| key | 说明 | 默认 |
|-----|------|------|
| `kb_dir` | KB 根目录（相对值基于 KB 根；`.` 表示 KB 根本身） | `.` |
| `search_note_embedding_url` | embedding 服务端点（语义检索用） | — |

`kb_dir` 解析规则（经 `katana_resolve_path`，基准为 `katana_kb_root` 而非 cwd）：
- 空 → KB 根（`katana_kb_root`）
- `.` → KB 根（join 后规整为 `<KB根>/.`，等价 KB 根）
- 相对路径 → `<KB根>/<kb_dir>`
- 绝对路径 / `~` 前缀 → 原样 / 展开 `$HOME`

## 路由与搜索范围

| 范围 | 检索方式 |
|------|----------|
| `Zettelkasten/`、`DeepThought/`、`转换文档/`、`WIKI.md`、`inbox/` | `wiki_search`（深读用 wiki MCP `fs_read`） |
| `智元工作/工作记录/` | `wf_search`（深读用 work-folder MCP `fs_read`） |
| `智元工作/op/`、`智元工作/具身中心工程OKR/` | 本 source 的本地只读检索 |
| `Ideas/`、`Templates/`、`Incubator/`、`docs/`、`.runtime/` 及其它未迁子树 | 本 source 的本地只读检索 |

> 操作事实（机器、repo、凭证 pointer、服务端点）已迁出 vault：memory card 归 katana-memory-mcp（`memory_index` / `memory_get(id)`）管理，不在本 skill 检索面内。

## 检索

只调用 `query_lancedb.py`；脚本在 vector、JSONL 和 live keyword 三条路径上都
自动排除已迁范围。不要调用覆盖全库的 vault-search endpoint，因为它无法证明
server-owned 范围已过滤。索引由 `vault-indexer` 保鲜，但命中仍受脚本范围锁约束。

```bash
# --source markdown 防 opencode 会话污染。
PY="$(katana_config_get search_note_python "python3" "")"
PY="${PY/#\~/$HOME}"
KB_DIR="$(katana_resolve_path "$(katana_config_get kb_dir "." "")")"
"$PY" "${CLAUDE_PLUGIN_ROOT}/skills/search-note/scripts/query_lancedb.py" "查询词" --mode auto --top-k 10 --source markdown --root "$KB_DIR"
```

## 只读约束

- 本 source 仅在未迁子树执行只读文件扫描和 vector DB 查询
- 已迁范围即使被 `--scope` 显式请求也会被脚本排除，必须改用对应 MCP
- 禁止任何写操作（写文件、git add/commit、修改索引）
- Markdown 文件是 source of truth；vector DB 只是派生索引

# References

- `WIKI.md` | source_type: internal | credibility: high — katana wiki plugin schema（`/wiki:query` 为优先检索路径）
