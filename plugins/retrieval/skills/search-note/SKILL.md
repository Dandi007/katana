---
name: search-note
description: 知识库只读检索路由。wiki/DeepThought 经 wiki_search，工作记录经 wf_search；仅未迁目录使用 scoped query_lancedb.py。
---

# /retrieval:search-note

知识库的只读检索路由。**任何情况下不写入 KB。** 已迁域只经对应
MCP，原生文件检索只覆盖明确列出的未迁子树。

## 配置（.katana）

| key | 说明 | 默认 |
|-----|------|------|
| `kb_dir` | 未迁目录的 client-local 检索根（不得用于已迁域） | `.` |
| `search_note_embedding_url` | embedding 服务端点（语义检索用） | — |

`kb_dir` 解析规则（经 `katana_resolve_path`，基准为 `katana_kb_root` 而非 cwd）：
- 空 → KB 根（`katana_kb_root`）
- `.` → KB 根（join 后规整为 `<KB根>/.`，等价 KB 根）
- 相对路径 → `<KB根>/<kb_dir>`
- 绝对路径 / `~` 前缀 → 原样 / 展开 `$HOME`

## 路由范围

| 范围 | 唯一检索方式 |
|------|--------------|
| wiki（`Zettelkasten/`、`DeepThought/`、`转换文档/`、`WIKI.md`、`inbox/`） | katana-wiki-mcp `wiki_search`；深挖用 wiki MCP `fs_read` |
| work-folder（`智元工作/工作记录/`） | katana-work-folder-mcp `wf_search`；深挖用 work-folder MCP `fs_read` |
| 未迁 `智元工作/` 内容（含 `op/`、`具身中心工程OKR/`，明确排除 `工作记录/`） | scoped local search |
| `Ideas/`、`Templates/`、`Incubator/`、`docs/`、`.runtime/` | scoped local search |

> 操作事实（机器、repo、凭证 pointer、服务端点）已迁出 vault：memory card 归 katana-memory-mcp（`memory_index` / `memory_get(id)`）管理，不在本 skill 检索面内。

## 检索

先按上表识别 domain。跨域问题分别调用 `wiki_search`、`wf_search` 和未迁域
local search，再合并并标注来源；绝不把三域送进同一个 filesystem root scan。

只有未迁域使用下面的 CLI。固定 include/exclude scope 是安全边界，不得删除；
vector cache 和 live fallback 都应用同一范围。

```bash
PY="$(katana_config_get search_note_python "python3" "")"
PY="${PY/#\~/$HOME}"
KB_DIR="$(katana_resolve_path "$(katana_config_get kb_dir "." "")")"
"$PY" "${CLAUDE_PLUGIN_ROOT}/skills/search-note/scripts/query_lancedb.py" \
  "查询词" --mode auto --top-k 10 --source markdown --root "$KB_DIR" \
  --scope "智元工作" --scope "Ideas" --scope "Templates" \
  --scope "Incubator" --scope "docs" --scope ".runtime" \
  --exclude-scope "智元工作/工作记录"
```

## 只读约束

- 已迁 wiki/work-folder 只调用 `wiki_search` / `wf_search` 和对应 MCP `fs_read`
- local CLI 只查询上表未迁子树，并强制排除迁出域
- 禁止任何写操作（写文件、git add/commit、修改索引）
- Markdown 文件是 source of truth；vector DB 只是派生索引

# References

- `WIKI.md` | source_type: internal | credibility: high — katana wiki plugin schema（`/wiki:query` 为优先检索路径）
