<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# writing:readability-check 工作流语义验收

判定 agent 对 readability-check 工作流（分几趟、冷读由谁执行）的解释是否正确。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否提及冷读这一趟（或等价：cold-read、可读性检查趟次）？
2. 回答是否明确说明冷读必须由 subagent（子 agent）来执行？
3. 回答是否解释了主 agent 不能自己冷读的原因（已知上下文/先验偏见，无法真正"冷"读）？
4. 整体描述与 readability-check skill 的实际工作流一致（无明显幻觉步骤）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
