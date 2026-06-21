<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# writing:using-writing 注入后路由语义验收

判定 using-writing skill 注入后，agent 能否正确路由 writing 相关操作。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否提及检查文档可读性应使用 writing: 命名空间下的 skill（如 writing:readability-check）？
2. 回答是否说明可读性冷读步骤必须由 subagent 执行？
3. 回答是否提及写文档前应先读 patterns 子目录（或等价：.katana-writing/patterns/）？
4. 三条路由建议均与 using-writing skill 注入的约定一致（无幻觉路由）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
