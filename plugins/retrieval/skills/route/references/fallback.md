# 降级协议

当主要检索方式不可用时的降级链。

| 信息源 | 主要方式 | 降级 1 | 降级 2 |
|-------|---------|--------|--------|
| search-note: wiki | `wiki_search` | wiki MCP `fs_glob` + `fs_read` | 明确报告 wiki MCP 不可用 |
| search-note: work-folder | `wf_search` | work-folder MCP `fs_glob` + `fs_read` | 明确报告 work-folder MCP 不可用 |
| search-note: 未迁目录 | scoped LanceDB | scoped keyword + filename match | scoped title match |
| web | agent 原生 web search | Context7 MCP（仅库文档） | 告知用户无网络搜索能力 |
| agent-session-search | LanceDB 语义搜索 | SQL keyword 匹配 | 文件系统 grep |
| feishu | lark-cli API | feishu legacy 脚本 | — |

## 通用规则

- 降级后的结果可信度不得高于 medium
- 所有方式都不可用时，明确说明无法检索
