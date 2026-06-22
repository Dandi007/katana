# Task 1.7 Report：wiki_interface 开关 + SessionStart hook mode-aware

## Status
DONE

## Commit
`c3c42a6` feat(wiki-mcp): wiki_interface=mcp gates SessionStart to short MCP trigger (skill default unchanged)

## 改动摘要

### `plugins/wiki/hooks/session-start`
在「确认 WIKI.md 存在」后、原读 SKILL.md 前插入 mode 分支：
- `WIKI_INTERFACE` 通过 `katana_config_get "wiki_interface" "skill" "KATANA_WIKI_INTERFACE"` 读取（优先级：env > .katana > 默认 skill）
- `mcp` 分支：注入 brief 指定的短触发器（含 `wiki_query`、`wiki_search`、`wiki_ingest_plan/apply`），不读 SKILL.md
- `skill`（默认）分支：原逻辑完全不变，读 SKILL.md 并做 `{{WIKI_ROOT}}` 占位替换
- 安全校验、路径解析、`escape_for_json`、`printf` 输出均未改动

### `plugins/wiki/tests/session-start-mcp-mode.test.sh`（新建）
仿 `session-start-inject.test.sh` 骨架（临时目录 + trap 清理 + ok/bad/exit 1）：
- Case A：`KATANA_WIKI_INTERFACE=mcp` env → 断言含 `wiki_query`、`wiki_ingest`，不含 `using-wiki`，JSON 格式正确
- Case A2：`.katana` 写 `wiki_interface=mcp` → 断言含 `wiki_query`（验证 .katana 读取路径）
- Case B：不设 wiki_interface（默认 skill）→ 断言含 `using-wiki`/`wiki:query`、JSON 正确、WIKI_ROOT 占位已替换（回归安全）

## 测试摘要
session-start-mcp-mode.test.sh  : 8 passing, 0 failing
session-start-inject.test.sh    : 4 passing, 0 failing
session-start-resolve.test.sh   : 2 passing, 0 failing
合计 14 PASS，0 FAIL

## Concern
无。短触发器硬编码在 hook 里；若将来文案需修改须同步更新测试中断言字符串。
