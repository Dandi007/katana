<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# writing:readability-check evolve 反馈分流语义验收

判定 agent 对 evolve 模式中不同类型反馈落盘位置的解释是否正确。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否提及结构类反馈（"缺少某章节"等）落到 Layout/template 相关文件？
2. 回答是否提及写法类反馈（"这一节怎么写更好"等）落到 guide/pattern 相关文件？
3. 回答是否提及评判标准类反馈落到 pattern 相关文件？
4. 三类反馈的落盘目标是否互相区分、无混淆？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
