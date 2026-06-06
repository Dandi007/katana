<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# wiki:lint 报告语义验收

逐项 yes/no + evidence（引用报告原文行）：
1. 报告点名的矛盾是否就是 92°C/96°C 温度分歧（而非幻觉出的其他矛盾）？
2. 报告引用的页面是否全部真实存在于 笔记/ 目录（无幻觉页名）？
3. 报告是否保持了矛盾的独立性（未把 92°C/96°C 调和/合并成单一结论）？
4. 孤儿页/provenance 检查结论与库的实际状态一致吗（fixture 设计为无孤儿、全部有 source）？

输出 fenced json：{"items": [{"q", "answer": "yes|no", "evidence"}]}
