# Work Folder

跨 session / 多阶段 / brainstorm→plan→execute 的持续性工作，先用 `wf_create` 建立或 `wf_resume` 绑定 work folder（查找用 `wf_search`）；control 文件只经 work-folder MCP 的 `fs_read` / `fs_write` / `fs_edit` 访问，存档用 `wf_save`。各 artifact 的格式、字段、读取优先级随 `work-folder:checkpoint` 调用载入（见其 `references/artifact-formats.md`），不在此常驻。不要解析或暴露 server 的物理根。
