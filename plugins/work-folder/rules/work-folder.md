# Work Folder MCP

Work Folder 只经 work-folder MCP 访问。`folder_id` 是 opaque token：只从 `wf_create`、`wf_search` 或 `wf_list` 的返回值取得并原样传给后续 tool；不得推导、拼接或解析为 client 路径，也不得用原生文件工具访问。生命周期用 `wf_create` / `wf_search` / `wf_list` / `wf_resume` / `wf_save`；文件按 `folder_id` + folder-relative `filename` 用 `fs_read` / `fs_stat` / `fs_list` / `fs_create` / `fs_write` / `fs_edit`。新文件用 `fs_create`；`fs_write` 只覆盖已存在文件，不会隐式创建。所有 mutation 由 MCP server 经治理事务自动 Git commit。`wf_resume` 返回 BROKEN 时必须停止，只报告阻塞并等待用户决策。
