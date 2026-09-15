---
name: ingest
description: 已退役的 Wiki 入库入口，仅提示使用当前 wiki-v3 MCP；不执行旧入库管线。
---

# Wiki 成稿入口

此 skill 已退役，不执行本地文件写入、raw层归档或旧 ingest pipeline。

直接使用 wiki-v3 MCP：

- `wiki_search` / `wiki_page_get` 核对已有文档；`wiki_template_list/get` 获取模板。
- 上游 Agent 完成标题、摘要和正文，使用 `wiki_page_validate` 预检。
- `wiki_page_create` 创建成稿；`wiki_page_update` 在明确用户授权范围内局部修改，每页携带当前 revision。
- 写请求携带稳定 request_id；同步与异步共享同一路径，`wiki_job_get` 查询结果和阻塞原因。
- 关联是 Markdown 中的相关 Wiki 列表，可用 page_update.related 按ID维护；不自动修改未授权正文。

服务端程序化校验不合规即返回reason，不调用管理员重写已完成的成稿。Markdown和运行状态均由服务管理，客户端不读取物理数据目录。

# References

- wiki-v3 MCP 的 template_list/get、page_validate、page_create/update、job_get 工具定义。
