<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# memory:remember 落库语义验收（当前 MCP semantics）

判定 memory:remember 是否通过 katana-memory-mcp 的 MCP tool 把 card 真正落库，
而不是仅回答用户或走已淘汰的文件系统 scanner。

输入：
- {case_trace}：本 case 的 stream-json trace。
- remember-result.txt：skill 写出的结果文件（应含服务返回的 card id 与 active 卡总数）。

逐项 yes/no + evidence（引用 trace 里的 tool_use 事件或结果文件原文）：
1. trace 中是否出现对 memory MCP 写入 tool（名字含 `memory_create` 或 `memory_update`）的调用？
2. remember-result.txt 是否记录了一个由服务生成的 card id（形如 `m-` 前缀的 id）？
3. remember-result.txt 是否报告了当前 active 卡总数（一个整数）？
4. 是否**没有**恢复文件系统/AWK scanner 行为（trace 中不出现直接读写 memory 卡文件的 scanner 脚本，如 scan-memory）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
