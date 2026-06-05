---
name: feishu
description: 飞书检索源。经 lark-cli 取文档/IM/wiki/base；凭证走 lark-cli 自身机制。
---

# /retrieval:feishu

飞书知识的检索源。通过 `lark-cli` 检索飞书文档、Wiki、IM 消息、多维表格（Base）。

**凭证完全由 lark-cli 自身管理（macOS Keychain + `~/.lark-cli/config.json`），不在 `.katana` 或任何 Git 追踪文件中存放。**

## 前提：lark-cli 可用性检查

```bash
command -v lark-cli >/dev/null 2>&1 || { echo "lark-cli absent, skip feishu source"; exit 0; }
lark-cli docs +fetch --help >/dev/null 2>&1 || { echo "lark-cli not configured, skip feishu source"; exit 0; }
```

## 检索操作

### 文档 / Wiki

```bash
# 按 URL 取文档正文
lark-cli docs +fetch <doc_url>

# Wiki 节点检索
lark-cli wiki +search "<keyword>"

# 列出 Wiki 空间
lark-cli wiki +list
```

### IM 消息搜索

```bash
# 跨群消息全文搜索（用户身份）
lark-cli messages +search "<keyword>"

# 按 message_id 批量拉取
lark-cli messages +mget <msg_id1> <msg_id2>

# 搜索群
lark-cli chats +search "<keyword>"
```

### 多维表格（Base）

```bash
# 列出 Base
lark-cli base +list

# 查询 Base 记录
lark-cli base +records <app_token> <table_id> --filter "<filter>"
```

## 安全约束

- 凭证不写入 `.katana`、Git 追踪文件或聊天输出
- lark-cli OAuth token 存于 macOS Keychain，按 AppID 隔离
- 写操作（发消息、创建文档）在本 source 中不触发——检索路径只读

# References

- lark-cli（host CLI，自身 OAuth/Keychain 管理凭证）
