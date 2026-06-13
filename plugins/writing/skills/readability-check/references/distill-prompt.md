# Distill 蒸馏指引（语料 → template + pattern 首稿）

输入：一个 type + N 篇该类型现有好文档（语料）。
输出：`template/<kind>.md` 首稿（Layout + 写作 guide）+ `patterns/<type>.md` 首稿（审的脸）。

## 步骤

1. **读全部语料**，逐篇拆出：frontmatter key 集合、H1/H2 标题序列、各节实际承载什么、L0 落点。
2. **蒸馏 Layout**：取语料里**稳定复现**的节与字段为骨架；偶发节标可选。顺序取多数派。
3. **蒸馏写作 guide**：把语料里写得好的共性（怎么开头、怎么分层、写偏的反例）提炼成 prompt 式指引。
4. **蒸馏 pattern**（审的脸）：把「好/不好」的判断维度落成机检项 + 冷读项 + 反模式。
5. **对齐外部 schema**：若该 type 有外部 SSoT（`WIKI.md`/SPEC-NNN），Layout 的 frontmatter/结构对齐并 link，不重定义。
6. 产物按 `references/template-spec.md` 的规格成形。

## 硬规则

- **人工 gate**：首稿一律先呈给用户 diff，**经确认才落盘**（防 model-collapse）。
- 语料 immutable，不改原文。
- 不臆造语料里不存在的节/字段。
