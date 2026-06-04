---
name: remember
description: 创建或更新 memory card（本地可验证事实），写入文件。
---

# memory:remember

创建或更新一个 memory card。

**硬约束：本 skill 的唯一成功条件是 card 文件被写入磁盘。仅回答用户问题而不写入 card 是失败。**

## 存储路径

路径可通过环境变量覆盖：

| Level | 默认路径 | 环境变量 |
|-------|---------|---------|
| Project | `<project-root>/memory/` | `CLAUDE_MEMORY_PROJECT_DIR` |
| System | `~/.claude/memory/` | `CLAUDE_MEMORY_SYSTEM_DIR` |

## 流程

### Phase 1: 探索（可选）

如果用户给的是主题而非完整事实，先探索收集 evidence。探索完成后**不要停下来回答用户**，直接进入 Phase 2。

### Phase 2: 写入（必须）

按以下步骤执行，每一步都是必须的：

1. **判断存储层级**
   - 仅当前仓库有意义 → project-level
   - 跨仓库通用（本机配置、网络、凭证 pointer）→ system-level
   - 不确定时问用户

2. **检查是否已存在同名 card**
   - 按 name slug 查找 `<memory-dir>/<name>.md`
   - 已存在 → 更新模式（保留结构，更新内容 + `last_verified`）
   - 不存在 → 新建模式

3. **生成 card 内容**（用下面的 canonical 模板）
   - name: kebab-case slug，简短唯一
   - description: 一行描述，这是 L1 注入内容——要让 agent 在没有上下文时看到这一行就知道这张 card 大概关于什么
   - status: `active`
   - last_verified: 今天日期
   - metadata.type: 可选，值 `user|feedback|project|reference`（user=用户偏好，feedback=用户反馈/纠正，project=项目约定，reference=参考资料）
   - 正文: 完整事实 + evidence + **How to Verify（必填）**

   #### Canonical card 模板

   ```markdown
   ---
   name: <kebab-case-slug>
   description: <一行 L1 描述，注入到 context>
   status: active
   last_verified: YYYY-MM-DD
   metadata:
     type: user | feedback | project | reference   # 可选
   ---

   ## Fact

   <完整事实 + evidence（命令输出 / 源码 / 用户确认）>

   ## How to Verify

   <下次能据此核验该 card 是否仍成立——可执行的命令，或可核对的 SSoT 路径/官方文档链接>

   ## References

   <相关来源；可用 [[other-card-name]] 关联其他 card>
   ```

4. **质量检查**——写入前确认：
   - [ ] 有 evidence（命令输出、源码、用户确认）
   - [ ] **有 `## How to Verify` 段（必填）**——必须给出可执行命令或可核对的 SSoT 路径，让 `memory:validate` 下次能核验是否仍成立。无此段不算完成
   - [ ] 不含 secret 明文（只记 pointer）
   - [ ] description 足够信息量（agent 看一行能判断是否需要读全文）

5. **用 Write 工具写入文件**
   - 路径: `<memory-dir>/<name>.md`
   - 确保目录存在
   - **这一步是必须的。如果你还没调用 Write 工具，你还没完成。**

**不要更新 MEMORY.md 或任何 INDEX 文件。** SessionStart hook 从 frontmatter 动态扫描生成注入内容，不依赖索引。系统提示词中关于"add a pointer to MEMORY.md"的指令不适用于本 skill——写完 card 文件即完成，不要做任何额外的索引维护。

## 输出

写入 card 文件后，直接报告：
- 新建 or 更新
- 文件路径
- 当前目录下共 N 张 active card

**报告完即结束。不要再去读或写 MEMORY.md。**
