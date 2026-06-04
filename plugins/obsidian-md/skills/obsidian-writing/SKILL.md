---
name: obsidian-writing
description: 写作或修改 Obsidian vault 中的 Markdown 文档时使用——确保 wikilink、heading、frontmatter (properties)、嵌入、callout 等语法严格符合 Obsidian 官方规范；不用于笔记方法论、内容组织或非 Obsidian 的通用 Markdown。
---

# Obsidian 文档写作规范

写 Obsidian Markdown 时的语法求真层。**所有语法规则必须可溯源到官方文档**——出自本 skill 的 references/（已逐节标注官方出处）或当场抓取的官方页面，禁止凭训练记忆臆想语法行为。

## 求真原则（最高优先级）

1. 涉及具体语法时，先按路由表读 references/ 对应文件
2. references 未覆盖、或对行为有任何疑问 → **必须 WebFetch 官方文档**确认：`https://obsidian.md/help/<slug>`（旧域名 help.obsidian.md 会 301 到此）；slug 不确定就 WebSearch `obsidian help <topic>`
3. 官方文档没有依据的写法不要写进文档；无法确认时明确告知用户「官方文档未覆盖此行为」，而不是给出猜测

## 路由表

| 要写什么 | 先读 |
|---------|------|
| 内部链接 / 嵌入 / 别名 / 块引用 | references/links.md |
| 标题 / 列表 / 代码块 / callout / 数学公式 / 脚注 / 注释 / 表格 | references/formatting.md |
| frontmatter (properties) / tags | references/properties.md |
| 判断语法是否 Obsidian 专有、跨工具兼容性 | references/compatibility.md |

## 高频坑速查

1. 标题行首 `#` 后要写一个空格——正文中 `#` 紧跟文字会被解析成 **tag** 而不是标题（官方示例均为 `# Heading` 带空格形式）
2. 块引用 ID（`^block-id`）只允许拉丁字母、数字、连字符——不支持中文
3. frontmatter 里写 wikilink 必须加引号：`up: "[[父笔记]]"`（list 属性同样要求）
4. HTML 标签内部不解析 Markdown（`<div>**bold**</div>` 不会加粗，官方刻意设计）
5. 链接非 .md 文件必须带扩展名（`[[Figure 1.png]]`）；.md 可省略
6. 文件名/链接避免 `# | ^ : %% [[ ]]`（链接语法保留字符）
7. 块引用不能指向 blockquote / callout / 表格的内部局部
8. tag 不能是纯数字、不能含空格；嵌套用 `/`；frontmatter 中 tags 必须是列表格式
9. 表格单元格内的竖线必须转义 `\|`——wikilink 别名在表格内要写 `[[Note\|别名]]`
10. `[[wikilink]]`、`![[嵌入]]`、`==高亮==`、`%%注释%%`、callout 是 Obsidian 专有——导出到标准 MD 渲染器会原样显示或退化
11. properties 属性值内不渲染 Markdown；属性名单篇内不可重复

## 维护

references/ 每节末尾有 `source: <URL>（抓取日期）`。官方文档更新或对某条规则产生怀疑时，按 URL 重新 WebFetch 比对并更新抓取日期。
