# retrieval — katana plugin

Multi-source information retrieval for Claude Code. Routes a natural-language query
to the right source(s) via intent detection, applies a credibility ladder for ranking,
and chains fallbacks so you always get an answer.

## 用途

给 Claude Code agent 提供统一的信息检索入口：

- **Web 搜索**：Exa / DuckDuckGo，带 credibility 过滤
- **Reddit**：社区讨论、技术经验贴
- **Twitter / X**：实时动态、开发者短文
- **代码搜索**：跨本地 repo 的语义/关键词检索
- **GitHub / GitLab**：Issue、PR、代码片段
- **Linear**：工单与项目进度
- **飞书（Feishu）**：企业文档与 IM 记录
- **search-note**：本地知识库语义检索

通用路由逻辑在插件内，个人鉴权与配置外置，不入 git。

## Skill 命名约定

```
/retrieval:SOURCE
```

示例：`/retrieval:web`、`/retrieval:github`、`/retrieval:search-note`

不带 SOURCE 时触发 intent routing，自动选择最合适的适配器。

## 配置

非密配置写项目根目录 `.katana` 文件（键值格式，行注释用 `#`）：

```ini
# 启用的源（逗号分隔）
retrieval_sources=web,reddit,twitter,official-docs,github,gitlab,linear,feishu,search-note,code,agent-session-search,xiaohongshu

# 本地知识库根（search-note 适配器用，只读检索输入）
kb_dir=.
```

完整配置键见 `CONFIG.md`（键名一律小写，与 `katana_config_get` 的精确匹配一致）。

配置优先级：**env var > .katana 文件 > 插件默认值**（由 `hooks/katana-config.sh` 的
`katana_config_get` 实现）。

## 密钥管理

持有密钥的环境变量在 host-local `~/.config/agent-shell/secrets.zsh` 中设置，
该文件 **不入 git**：

```bash
# 示例（文件不要 commit）
export EXA_API_KEY="..."
export FEISHU_APP_ID="..."
export FEISHU_APP_SECRET="..."
export LINEAR_API_KEY="..."
```

插件通过 `katana_config_get` 的第三个参数（`env_var`）读取上述变量。

## E2E 测试

```bash
tests/run-e2e.sh
```

在项目根目录执行；需要先配置好上述密钥。
