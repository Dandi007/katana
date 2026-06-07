<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# fpa:fpa 完整运行语义验收

逐项 yes/no + evidence（引用文档原文段落或行）：

1. Adversarial Review 节中每条 verdict 是否都有带出处的 evidence（URL / 文件路径 / commit hash，而非空泛陈述）？
2. Constraint Classification 表里 hard/soft/assumption 的分类是否与各自 Evidence 列自洽（hard constraint 有不可绕过的证据支撑，assumption 明确标注待验证）？
3. Key Insight 是否指出了一个具体的默认假设（而非泛化空话），并说明该假设是否被对抗验证修订或确认？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
