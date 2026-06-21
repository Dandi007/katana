<!-- 正极性：yes=符合预期；judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# jury:review 冒烟语义验收
逐项 yes/no + evidence：
1. 报告是否如实保留了各模型的独立意见（未被合并成单一结论）？
2. 各模型意见是否确实针对被评审的 diff（非泛泛而谈、非复读 prompt）？
输出 fenced json：{"items":[{"q":"...","answer":"yes|no","evidence":"..."}]}
