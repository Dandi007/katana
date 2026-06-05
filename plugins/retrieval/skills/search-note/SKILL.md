---
name: search-note
description: 本地知识库检索源（只读）。原子笔记/Index/工作记录/facts；query_lancedb.py --mode auto（vector→keyword 自降级）。
---

# /retrieval:search-note

本地 Markdown 知识库的只读检索源。**任何情况下不写入 KB。**

## 配置（.katana）

| key | 说明 | 默认 |
|-----|------|------|
| `kb_dir` | KB 根目录（`.` 表示 `CLAUDE_PROJECT_DIR`） | `.` |
| `search_note_embedding_url` | embedding 服务端点（语义检索用） | — |

`kb_dir` 解析规则：
- `.` 或空 → `$CLAUDE_PROJECT_DIR`
- 相对路径 → `$CLAUDE_PROJECT_DIR/<kb_dir>`
- 绝对路径 → 原值

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
| `memory/` | 操作事实（机器、repo、凭证 pointer、服务端点） |

## 检索

用 `query_lancedb.py --mode auto`：索引可用走 vector，不可用自动降级 keyword。脚本自身处理 vector→keyword 降级，无需外部 grep tier。

索引位于 `~/.cache/agent-knowledge/Zettelkasten/lancedb/`（不在 iCloud 内）。

```bash
# 语义检索（脚本内部 --mode auto：索引可用走 vector，不可用自动降级 keyword）
PY="$(katana_config_get search_note_python "python3" "")"
PY="${PY/#\~/$HOME}"   # .katana 里的 ~ 不会被自动展开，须手动展（同 twitter profile / wiki hook 套路）
"$PY" "${CLAUDE_PLUGIN_ROOT}/skills/search-note/scripts/query_lancedb.py" "查询词" --mode auto --top-k 10
```

embedding 端点从 `.katana` 读取：
```bash
EMBED_URL="$(katana_config_get search_note_embedding_url "" "")"
```

## 只读约束

- 本 source 只执行 `grep`、`find`、读文件、vector DB 查询
- 禁止任何写操作（写文件、git add/commit、修改索引）
- Markdown 文件是 source of truth；vector DB 只是派生索引

# References

- `WIKI.md` | source_type: internal | credibility: high — katana wiki plugin schema（`/wiki:query` 为优先检索路径）
