# Fleet 基建工具修复

## 范围与结果

本 SPEC 对应 WF `wf-fdb41f`，修复恢复入口和监督读数中的四个确定性问题：

- context 路径格以明确的 Markdown code span 或链接开头时，尾部说明不参与存在性校验；裸路径中的空格和括号继续保留。分支校验与 BROKEN 规则保持原有语义。
- `fs_create` 拒绝创建 `golden-order.md` 时，给出 `wf_save(folder_id=..., golden_order_additions=...)` 的准确入口。创建与追加仍经既有生命周期和 append-only 写门。
- 监督读数按 generation 数字顺序选取事件，避免 `g10` 排在 `g2` 前；继续包含第一代根目录及后续代目录。
- Monitor 对新单尚无 `status.json` 的情况安静跳过，继续处理其他单；沿用 PR #160 的局部修复。

## 验收与部署

WF 测试涵盖真实含空格/括号目录、明确包装带说明、裸路径保真、拒绝消息给出的生命周期操作可实际成功。监督测试执行真实读数代码段与实际 autowake 函数，使用临时记录，不访问生产。

发布包含两个产物：Work Folder MCP 不可变源码快照及独立 `line-supervisor` 插件。MCP 仅升级代码，无数据迁移；保留旧 unit 的解释器、配置和旧快照回滚信息，经候选进程验证后再切服务。插件从此提交安装，不能只覆盖宿主 cache，否则重装会丢失修复。`main` 保持人工 PR 合入纪律。

# References

- WF `wf-6d1cea/findings.md` 开线问题 5、6；WF `wf-fdb41f`。
- [Katana PR #160](https://github.com/Dandi007/katana/pull/160)：新单状态缓存缺失处理。
- [Katana PR #157](https://github.com/Dandi007/katana/pull/157)：后续 generation 事件目录。
