---
name: code
description: 代码求真源。Use when 要确认代码入口/调用链/配置来源/边界条件/实际行为，或验证文档与代码是否一致。以源码为 SSoT。
---

# /retrieval:code

代码永远是真相；文档与认知需被验证，不一致时以代码为准。

## 代码在哪（按序）

1. **本地 code root 直读（主路）**：`$AGENT_CODE_ROOT/{third_party,self,company}/`，回退 `$AGENT_CODE_ROOT_FALLBACK/...`。操作前 `[ -n "$AGENT_CODE_ROOT" ] && [ -d "$AGENT_CODE_ROOT" ]` 判可用。
2. 用户给的本地路径：先读其 `AGENTS.md`/`CLAUDE.md`。
3. **本地没有 → 搜 repo**：经 /retrieval:github、/retrieval:gitlab 或原生 WebSearch 定位。
4. **自动 clone ingest（默认）**：搜到/需要但本地缺的 repo，**默认自动** `git clone` 到 `$AGENT_CODE_ROOT/<code_clone_category_default>/<repo>`（默认 third_party），再从本地读。非 opt-in。

## 约束

- 代码只落 code root 分类目录，**绝不进知识库 repo**。
- repo 级总览/架构/安装 → analyze-source-repo；有 master skill 的项目走对应 master skill。

配置：`code_root_env`、`code_root_fallback_env`、`code_clone_category_default`（.katana）。
