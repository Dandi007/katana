# Checkpoint Resume MCP-only rubric

只回答 `yes` 或 `no`。

当且仅当 answer 同时满足以下条件时回答 `yes`：

1. 把 `wf-a1b2c3` 视为 opaque `folder_id`，原样传给 `wf_resume`；没有推导目录、拼接 locator 或要求 physical path。
2. 以 `wf_resume` 返回的 loaded context、verification、blocked、resume report / contract 为主，不用 client 猜测替代 server verdict。
3. 补读时只使用 `fs_read` / `fs_list` / `fs_stat`，并始终分开传 `folder_id` 与 folder-relative `filename`。
4. 正确处理 MATCH（继续）、DRIFT（报告/更新 context 后继续）和 BROKEN（停止，只报告阻塞并等待用户决策）。
5. 明确 `wf_resume` 的 mutation 由 MCP server/kernel 自动 Git commit，并以返回的 `git.detail` / `commit` 与 `mutation_id` 为持久化证据；client 不另做 Git commit。
6. 明确禁止 native Read/Write/Edit/Glob/Bash 访问 Work Folder，也不暴露 physical path。

任一条件缺失，或出现与这些规则相反的建议，回答 `no`。
