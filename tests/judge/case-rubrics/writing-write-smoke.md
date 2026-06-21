<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# writing:write 写前/写后步骤语义验收

判定 agent 对 writing:write skill 写前步骤和写后步骤的解释是否正确。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否提及写前需读取 patterns 相关文件（.katana-writing/patterns/ 或等价）？
2. 回答是否提及写后需要做自检（或等价：self-check、对照 pattern 检查）？
3. 写前步骤和写后步骤均有清晰描述，未混淆顺序？
4. 描述与 writing:write skill 实际约定一致（无幻觉步骤）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
