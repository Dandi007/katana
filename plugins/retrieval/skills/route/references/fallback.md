# 降级协议

当主要检索方式不可用时的降级链。

| 信息源 | 主要方式 | 降级 1 | 降级 2 |
|-------|---------|--------|--------|
| search-note（仅未迁子树） | LanceDB 语义搜索 | keyword + filename 匹配 | 目录遍历 + title 匹配（脚本强制排除已迁域） |
| wiki | `wiki_search` | wiki MCP `wiki_page_list` + `wiki_page_get` | 明确报告 MCP 不可用，不回落 client fs |
| work-folder | `wf_search` | work-folder MCP `fs_glob` + `fs_read` | 明确报告 MCP 不可用，不回落 client fs |
| web | agent 原生 web search | Context7 MCP（仅库文档） | 告知用户无网络搜索能力 |
| feishu | lark-cli API | feishu legacy 脚本 | — |

## 通用规则

- 降级后的结果可信度不得高于 medium
- 所有方式都不可用时，明确说明无法检索
