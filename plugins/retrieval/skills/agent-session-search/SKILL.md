---
name: agent-session-search
description: Agent 会话历史检索源。语义→SQL→grep 降级查历史会话；存储路径走 AGENT_SESSION_STORE/默认。
---

# /retrieval:agent-session-search

跨 provider（Claude Code、OpenCode 等）检索 agent 历史会话记录。回答"之前讨论过 X 吗"、"当时为什么这么决策"。

## 存储路径

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AGENT_SESSION_STORE` | `~/.claude/projects` | Claude Code 会话根目录 |

```bash
SS="${AGENT_SESSION_STORE:-$HOME/.claude/projects}"
```

## 检索降级链

### 主路：语义检索

若 embedding 服务可用（`search_note_embedding_url`），对会话摘要建索引后语义匹配。

### 降级 1：SQL（OpenCode）

OpenCode 会话存于 SQLite：

```bash
DB="$HOME/.local/share/opencode/opencode.db"
if [ -f "$DB" ]; then
  sqlite3 "$DB" "SELECT id, summary FROM sessions WHERE summary LIKE '%<keyword>%' LIMIT 20;"
fi
```

### 降级 2：grep（Claude Code）

```bash
SS="${AGENT_SESSION_STORE:-$HOME/.claude/projects}"
grep -rl "<keyword>" "$SS" 2>/dev/null | head -20
```

grep 命中后读取对应 JSONL 文件还原对话片段：

```bash
grep -h "<keyword>" <matched_file> | head -40
```

## 边界

- 本 source 只读——不修改、不删除任何会话文件
- token 消耗统计 → LLM Usage Dashboard（独立项目），不在本 source 中处理
- 本地 Markdown 知识 → `search-note` source
- OpenCode runtime 机制 → `opencode-master` skill

# References

- `.agents/skills/agent-session-search/SKILL.md` | source_type: internal | credibility: high — 原 skill 定义
