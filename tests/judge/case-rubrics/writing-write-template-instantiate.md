<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# writing:write template 实例化语义验收

判定 agent 对 writing:write skill 命中 template 后如何搭骨架的解释是否正确。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否提及命中 kind 后会读取对应 template 文件来搭骨架？
2. 回答是否说明产出结构由 Layout（template 骨架）来保证？
3. 回答是否提及没有对应 template 时的回退策略（走 distill 冷启动或等价）？
4. 整体流程描述与 writing:write skill 实际约定一致（无幻觉步骤）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
