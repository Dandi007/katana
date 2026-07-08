---
name: search-note
description: 本地知识库检索源（只读）。原子笔记/Index/工作记录/facts；query_lancedb.py --mode auto（vector→keyword 自降级）。
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

## 搜索范围

| 目录 | 内容 |
|------|------|
| `Zettelkasten/` | 技术概念、原子笔记、Index 导航入口 |
| `Zettelkasten/Index/` | 主题索引，命中时优先展示 |
| `DeepThought/` | 深度研究产出 |
| `智元工作/` | 工作文档、汇报、会议、方案 |
| `智元工作/工作记录/` | 排查、推进、配置、执行过程 |
| `智元工作/op/` | One Page、跨系统汇报材料 |
| `智元工作/具身中心工程OKR/` | OKR、团队规划 |

> 操作事实（机器、repo、凭证 pointer、服务端点）已迁出 vault：memory card 归 katana-memory-mcp（`memory_index` / `memory_get(id)`）管理，不在本 skill 检索面内。

## 检索

优先打本机 **vault-search 服务**（常驻 svc，含 vector+keyword RRF 混合检索，质量优于裸 `query_lancedb.py`）；服务不可达时回落 CLI `query_lancedb.py`。search-note 搜**全量语料**。

服务/索引引擎归 `Dandi007/agent-knowledge`；索引 `~/.cache/agent-knowledge/Zettelkasten/lancedb/`（不在 iCloud 内），由 `vault-indexer` svc 每晚增量保鲜。

```bash
# 主路：vault-search 服务（POST /search → {results:[{path,score,title,snippet}], mode}）
resp="$(curl -s -m 8 -X POST http://127.0.0.1:18082/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"查询词","top_k":10}')"
if [ -n "$resp" ] && printf '%s' "$resp" | grep -q '"results"'; then
  printf '%s\n' "$resp"
else
  # 回落：CLI（服务没起时不致命）。--source markdown 防 opencode 会话污染。
  PY="$(katana_config_get search_note_python "python3" "")"
  PY="${PY/#\~/$HOME}"   # .katana 里的 ~ 不会被自动展开，须手动展
  # kb_dir 经 katana_resolve_path 解析成绝对 KB 根（基准 katana_kb_root，非 cwd），
  # 显式传 --root，使 keyword 降级扫描根锁定 KB 而非当前工作目录。
  KB_DIR="$(katana_resolve_path "$(katana_config_get kb_dir "." "")")"
  "$PY" "${CLAUDE_PLUGIN_ROOT}/skills/search-note/scripts/query_lancedb.py" "查询词" --mode auto --top-k 10 --source markdown --root "$KB_DIR"
fi
```

## 只读约束

- 本 source 只执行 `grep`、`find`、读文件、vector DB 查询
- 禁止任何写操作（写文件、git add/commit、修改索引）
- Markdown 文件是 source of truth；vector DB 只是派生索引

# References

- `WIKI.md` | source_type: internal | credibility: high — katana wiki plugin schema（`/wiki:query` 为优先检索路径）
