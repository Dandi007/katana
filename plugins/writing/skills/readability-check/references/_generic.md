# 无类型兜底维度（_generic）

当被检文档不命中任何 typed pattern 时用这套通用可读性维度。它是 `writing:bluf`（结构）+ 冷读自包含性 的交集，不针对任何具体文档类型。

> 检完务必 offer：若这类文档高频出现，是否值得为它起一份 typed pattern（当前项目 writing_dir 下的 patterns/<新type>.md，writing_dir 由 using-writing 注入，见 session 开头），让它进入快车道。

## 机检维度（标 [机检]）

直接套 `self-containment-checklist.md` 的静态检查（能 grep 的都给了命令）：

- [机检] 未解释的内部编号（F17 / §08 / Q-XXX / bg_* / ses_* / MR !* / FI-*）
- [机检] 未解释的历史引用（"用户原话" / "X 日讲过" / "上次 session" / checkpoint）
- [机检] 未落地的跨引用（"见 xxx.md §X" / "承接 yyy 报告"）
- [机检] 内网 URL / 私网 IP（外发文档读者打不开）
- [机检] 文档开头是否有「读者与范围」节（目标读者 / 覆盖 / 不覆盖 / 前置 / 当前状态）
- [机检] 高频术语是否有术语节解释（覆盖前 30% 出现的术语）
- [机检] 章节编号是否连续、有无前向引用
- [机检] BLUF：首句是否 assertion（不是"随着……的发展"式铺垫）
- [机检] bluf Tier1 banned phrases（hedging / 空洞连接词连发 / meta-commentary / 空洞开场 / 过度礼貌）

## 冷读维度（标 [冷读]，交 subagent）

- [冷读] 读完能否用自己的话说出：为谁写、要做/要点是什么、关键约束、开放问题
- [冷读] 信息分层是否合理（L0 一句话结论 → L1 3–5 bullet → L2 正文 → L3 附录），有没有 info dumping
- [冷读] 是否 evidence-first（按调查/思考顺序铺陈）而非 reader-first（先结论后证据）
- [冷读] 请求/决策点是否被埋在中后段（buried ask）
- [冷读] 跨引用假设读者打不开外部文件时，当前段落还能否理解
- [冷读] 表格列名是否自解释、字段是否有 schema
- [冷读] 可执行性：被指派据此实施/评审的人，能否不追问作者就开工

## 严重度

按「不修是否直接导致误读 / 是否阻塞使用」分 P0/P1/P2，与 typed 流程一致。

## 参考
- `writing:bluf` — L0–L3、文档类型 L0 适配、Tier1/2/3 反模式
- `self-containment-checklist.md` — 机检命令全集
- `cold-read.md` — 冷读 subagent 机制
