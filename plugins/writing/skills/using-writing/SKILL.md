---
name: using-writing
description: Hook-injected convention layer for the writing plugin. Active when writing_dir is set; governs the write/review gate, where project-specific patterns/opinions live, and the cold-read iron rules.
---

# Using Writing

本项目启用 writing（知识库 `{{WRITING_DIR}}`）。两条 gate：① 写/改任何文档先走 `/writing:write`、先实例化对应 template，勿裸写；② 可读性冷读必须派 subagent（prompt 写明禁读文件名 + 读者角色），主 agent 自带上下文不可信。越界指路：事实核对→`verify`、论证质疑→`critical-review`、去 AI 味→`humanizer-zh`。路由（write/readability-check/bluf）、子目录语义、进化 gate（raw 不可变、人工确认防 model-collapse）随 `/writing:*` 调用载入。
