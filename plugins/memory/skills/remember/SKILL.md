---
name: remember
description: 创建或更新 memory card（本地可验证事实），通过 MCP tools 写入服务。
---

# memory:remember

创建或更新一个 memory card。

**硬约束：本 skill 的唯一成功条件是 card 落库（服务端持久化）。仅回答用户问题而不调用 MCP tool 写入 card 是失败。**

## 流程

### Phase 1: 探索（可选）

如果用户给的是主题而非完整事实，先探索收集 evidence。探索完成后**不要停下来回答用户**，直接进入 Phase 2。

### Phase 2: 写入（必须）

按以下步骤执行，每一步都是必须的：

1. **检查是否已存在同名/同主题 card**
   - 调用 `memory_index` 获取当前全量 card 列表（含 id、name、description）
   - 按 name slug 或主题关键词判断是否已有匹配 card
   - 已存在 → 更新模式：调用 `memory_get(id)` 读取全文，再调用 `memory_update(id, ...)` 更新内容 + `last_verified`
   - 不存在 → 新建模式：调用 `memory_create(name, description, body, type?)`

2. **生成 card 内容**（更新模式：在 Step 1 读取的旧内容基础上生成变更；新建模式：从零构建完整 body）
   - name: kebab-case slug，简短唯一
   - description: 一行描述，这是 L1 注入内容——要让 agent 在没有上下文时看到这一行就知道这张 card 大概关于什么
   - last_verified: 今天日期
   - type: 可选，值 `user|feedback|project|reference`（user=用户偏好，feedback=用户反馈/纠正，project=项目约定，reference=参考资料）
   - 正文: 完整事实 + evidence + **How to Verify（必填）**

   #### Canonical card 模板（body 部分，frontmatter 由服务生成）

   ```markdown
   ## Fact

   <完整事实 + evidence（命令输出 / 源码 / 用户确认）>

   ## How to Verify

   <下次能据此核验该 card 是否仍成立——可执行的命令，或可核对的 SSoT 路径/官方文档链接>

   ## References

   <相关来源；可用 [[other-card-name]] 关联其他 card>
   ```

3. **质量检查**——写入前确认：
   - [ ] 有 evidence（命令输出、源码、用户确认）
   - [ ] **有 `## How to Verify` 段（必填）**——必须给出可执行命令或可核对的 SSoT 路径，让 `memory:validate` 下次能核验是否仍成立。无此段不算完成
   - [ ] 不含 secret 明文（只记 pointer）
   - [ ] description 足够信息量（agent 看一行能判断是否需要读全文）

4. **调用 MCP tool 写入**
   - 新建：`memory_create(name, description, body, type?)`
   - 更新：`memory_update(id, description?, body?, last_verified?, status?)`
   - **这一步是必须的。如果你还没调用 MCP tool，你还没完成。**
   - 若找不到 `memory_create` / `memory_update` 等 MCP tool，提示用户检查 katana-memory-mcp 服务是否在运行（默认 `http://127.0.0.1:5604`，tenant `uther`）

## 输出

写入成功后，直接报告：
- 新建 or 更新
- card id（由服务返回）
- 当前 active 卡总数（从 `memory_index` 返回结果中统计）

**报告完即结束。**
