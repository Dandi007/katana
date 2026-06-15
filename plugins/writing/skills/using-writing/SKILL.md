---
name: using-writing
description: Hook-injected convention layer for the writing plugin. Active when writing_dir is set; governs the write/review gate, where project-specific patterns/opinions live, and the cold-read iron rules.
---

# Using Writing

本项目已启用 writing 工具套件；写作知识库在 `{{WRITING_DIR}}`（template/patterns/improvements/staging）。

## 调用前必须遵守（gate）

1. **不得无 template 裸写。** 起草/改写任何文档前先识别 kind、实例化 `{{WRITING_DIR}}/template/<kind>.md`，再校准 pattern + active improvements——直接凭感觉起草是反模式。
2. **冷读必须用 subagent，主 agent 不可信。** 可读性审视的冷读环节必须派 subagent，并在 prompt 层写明具体禁读文件名与阅读者角色。

> 越界指路：事实核对→`verify`；论证质疑→`critical-review`；去 AI 味→`humanizer-zh`/`humanizer`。

详细路由（write / readability-check / bluf）、子目录语义、进化 gate（raw immutable、人工确认防 model-collapse）随对应 `/writing:*` 调用时其 SKILL.md 载入，不在此常驻。
