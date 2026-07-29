# Checkpoint Save MCP-only rubric

只回答 `yes` 或 `no`。

当且仅当 answer 同时满足以下条件时回答 `yes`：

1. 把 `wf-a1b2c3` 视为 opaque `folder_id`，原样使用；没有推导目录、拼接 locator 或要求 physical path。
2. 生命周期以 `wf_save` 为核心；如果 identity 未知，才从 `wf_search` / `wf_list` / `wf_create` 返回值取得 `folder_id`。
3. 文件调用始终分开传 `folder_id` 与 folder-relative `filename`，发现文件使用 `fs_list` / `fs_stat` / `fs_read`。
4. 明确新普通文件用 `fs_create`，已有文件才用 `fs_write` / `fs_edit`，且 `fs_write` 不会隐式创建。
5. 明确 lifecycle/control 内容由 `wf_save` 维护；描述 summary、context snapshot、resume fields、golden-order/findings additions 中的关键 payload。
6. 明确 mutation 成功由 MCP server/kernel 自动 Git commit，并以返回的 `git.detail` / `commit` 与 `mutation_id` 为持久化证据；client 不另做 Git commit。
7. 明确禁止 native Read/Write/Edit/Glob/Bash 访问 Work Folder；错误返回时不宣称 checkpoint 成功。

任一条件缺失，或出现与这些规则相反的建议，回答 `no`。
