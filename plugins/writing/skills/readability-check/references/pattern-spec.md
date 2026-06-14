# Pattern 文件规格（审的脸 SSoT）

一份 `<writing_dir>/patterns/<type>.md` 是某个**文档类型族**的「审的脸」+ 共享层，是
**评判 / 适用判定 / 反模式** 的唯一 SSoT。`writing:readability-check` 命中 type 后读这一份。
与 `template-spec.md`（写的脸 SSoT）对位：template 管「怎么写」，pattern 管「怎么判、何时用」。

## 固定骨架（顺序固定；标注「按需」外的节缺失即不合规）

1. **`# Pattern: <中文名>（<type>）`** + 一段 blockquote：两张脸说明 + 本 type 覆盖哪些 kind。
2. **`## 适用判定（共享）`** —— 路径 / frontmatter / 内容特征锚点 + `**不是**…` 的 route-out。
   多 kind 时加 `### kind 区分速记`（每条给「特征 → kind」）。
3. **`## 写的脸（generative）→ writing:write`** —— **只一行指针**：命中 kind 后读对应
   `template/<kind>.md`，本 pattern **不复制结构骨架**。结构/写法的 SSoT 在 template，
   pattern 里再抄一遍 = Focus 双写（改一处忘另一处的漂移源）。
4. **`## 审的脸（evaluative）→ writing:readability-check checklist`** —— `### 共享`（所有 kind 都查）
   + 可选 `### kind 特异`。每条标 `[机检]` 或 `[冷读]`，精确可判，不写「建议改清楚些」这种空话。
5. **`## 反模式`** —— 命中即出 finding 的写法，给可识别特征。
6. **`## 演进记录（→ improvements/）`** —— 关联的 `状态: active` 演进卡 + 检索条件（`文档类型: <x>`）。
   **按需节**：该 type 已有 active 演进卡才出现；无卡则整节省略，不留空节（空节即 Focus 噪声）。
7. **`## 参考`** —— 对应 `template/<kind>.md`、外部权威源、`writing:bluf`、`self-containment-checklist.md`。

## 可选扩展节（有定义的槽位，不滥增）

- **`## 与 <相邻 type> 的区别（防误判）`** —— 易混类型补一节判别（如 `tech-standard` ↔ `tech-spec`）。
- **`## 已知变体（语料实证，非反模式）`** —— 该 type 有多种合法形态时列出，区别于反模式。

> 扩展节插在「适用判定」之后、「反模式」之前；不在骨架里的新节先回到本规格讨论，别就地发明。

## SSoT 边界（硬切，防漂移）

- **结构 / 写法**只活在 `template/<kind>.md`；pattern **只收**「怎么判（checklist）+ 何时适用 + 反模式」。
- **写的脸节恒为指针**，禁抄 template 骨架——违反即 Focus 双写。
- 评判规则的增量经 `evolve` 落本文件 checklist + `improvements/` 演进卡，不绕过人工 gate。

## 与 template / improvements 的关系

- `pattern ↔ template` 一对多：一份评判 pattern（type 族）覆盖多份生成 template（具体 kind）；
  kind 间共性进 `### 共享`，差异进 `### kind 特异`。
- `pattern ↔ improvements`：演进卡是反馈历史，pattern 是被它们提炼收敛后的当前态。

## 自检（distill / evolve 落 pattern 后跑一遍）

- [ ] 必含节齐（标题/适用判定/写的脸/审的脸/反模式/参考）、顺序对、节名用规格措辞（不是 `## 自我进化` 这类异化名）
- [ ] 演进记录：有 active 卡才留、无卡则省（不留空节）
- [ ] 写的脸节是一行指针，没抄 template 骨架
- [ ] 每条 checklist 标了 `[机检]`/`[冷读]` 且可判
- [ ] 适用判定有 route-out（`**不是**…`）
- [ ] 多 kind 的差异归到 `### kind 特异`，没混进共享
