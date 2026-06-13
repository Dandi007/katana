# Template 文件规格（写的脸 SSoT）

一份 `<writing_dir>/template/<kind>.md` 是某个**具体文档种类**的「写的脸」，是结构的唯一 SSoT。
`writing:write` 命中 kind 后读这一份就能写。含且仅含两个 section。

## `## Layout`（字面骨架，照抄即用）

- **frontmatter 块**：字面列出 key。外部已有 schema 的类型（根 `WIKI.md` 的原子笔记 schema、`write-spec` 的 SPEC-NNN），frontmatter 与结构**对齐并 link 到该外部源，绝不重定义**。
- **字面 H1/H2 标题**，按规定顺序排列。
- **L0 位置标注**：标明首屏/首句承载的 L0 结论。
- **内嵌「怎么填」微提示**：HTML 注释（如 `<!-- 一句话定义，不是话题词 -->`），实例化后逐条删除。

## `## 写作 guide（prompt）`

- 每节填写指引：要回答什么、好的样子、常见写偏。
- L0 适配（接 `writing:bluf` 对应类型行）。
- 该 kind 特有写法与硬约束。
- 外部权威源链接（WIKI.md schema / SPEC-NNN / `智元工作/CLAUDE.md`）。

## SSoT 边界（硬切，防漂移）

- **结构**（有哪些节 / frontmatter key / 顺序）只活在本文件的 `## Layout`。
- **怎么写**活在本文件的 `## 写作 guide`。
- **怎么判**活在 `patterns/<type>.md`（审的脸）——不在 template 里重复。

## 与 pattern 的关系

- `pattern ↔ template` 一对多：一份评判 pattern（type 族）覆盖多份生成 template（具体 kind）。
- template 不写适用判定/反模式/checklist——那些在 pattern。
