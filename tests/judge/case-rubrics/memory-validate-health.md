<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# memory:validate 健康核验语义验收（当前 MCP semantics）

判定 memory:validate 是否通过 katana-memory-mcp 的 MCP tool 读取并核验 card，
默认不改写，且不恢复已淘汰的文件系统 scanner。

输入：
- {case_trace}：本 case 的 stream-json trace。
- validate-report.txt：skill 写出的裁决统计与是否改写说明。

逐项 yes/no + evidence（引用 trace 里的 tool_use 事件或结果文件原文）：
1. trace 中是否出现对 memory MCP 读取 tool（名字含 `memory_index`）的调用？
2. validate-report.txt 是否给出裁决统计（至少含 verified / contradicted / unverifiable / stale / incomplete 中的多类计数）？
3. validate-report.txt 是否说明**默认未改写**任何 card（未在无用户确认下调用 memory_update 改正文/状态）？
4. 是否**没有**恢复文件系统/AWK scanner 行为（trace 中不出现 scan-memory 之类直接扫卡文件的脚本）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
