# 内部链接 / 嵌入 / 别名 / 块引用

> 本文件全部规则提炼自 Obsidian 官方文档（obsidian.md/help），每节末尾标注出处。官方未覆盖的行为不要假设——存疑时 WebFetch 出处 URL 重新确认。

## Wikilink 基础语法

- 两种等价写法：`[[Note]]`（wikilink，默认）与 `[文字](Note.md)`（Markdown 风格）
- wikilink 中 `.md` 扩展名可省略：`[[Note]]` 与 `[[Note.md]]` 等价
- Markdown 风格链接中空格必须 URL 编码为 `%20`
- **非 Markdown 文件的链接必须带扩展名**：官方原文 "links to file formats other than Markdown needs to include a file extension, such as `[[Figure 1.png]]`"

> source: https://obsidian.md/help/links（抓取 2026-06-04）

## 链接到标题（锚点）

- 其他文件的标题：`[[Note#Heading]]`
- 多级子标题：`[[Note#H1#H2#H3]]`（逐级用 `#` 连接）
- 同文件内锚点：`[[#Heading]]`

> source: https://obsidian.md/help/links（抓取 2026-06-04）

## 块引用（`^block-id`）

- 语法：`[[Note#^block-id]]`；在目标段落尾部加 ` ^block-id` 定义块（也可由 Obsidian 自动生成）
- **块标识符合法字符**：官方原文 "Block identifiers can only consist of Latin letters, numbers, and dashes."——仅拉丁字母、数字、连字符，不支持中文
- **不支持的目标**：官方原文 "We do not support links to specific parts of quotations, callouts, and tables."——不能链接到引用块、callout、表格的内部局部

> source: https://obsidian.md/help/links（抓取 2026-06-04）

## 显示别名与 aliases 属性

- 链接显示别名：wikilink 用竖线 `[[Note|显示文字]]`；Markdown 风格直接 `[显示文字](Note.md)`
- 笔记级别名：在 frontmatter 中以 **YAML 列表**格式声明（官方要求 "别名应始终格式化为 YAML 中的列表"）：

  ```yaml
  ---
  aliases:
    - 别名1
    - 别名2
  ---
  ```

- 通过别名自动补全插入链接时，Obsidian 生成 `[[原始名|别名]]` 完整格式（而非 `[[别名]]`），以保证与其他 wikilink 应用兼容
- 仅需改变单个链接显示文本时用 `|`，不要为此新增 alias

> source: https://obsidian.md/help/links + https://obsidian.md/help/aliases（抓取 2026-06-04）

## 嵌入（`![[]]`）

在内部链接前加 `!` 即嵌入内容，与源文件保持同步：

| 目标 | 语法 |
|------|------|
| 整篇笔记 | `![[Internal links]]` |
| 笔记的某标题 | `![[Internal links#Link to a heading]]` |
| 笔记的某块 | `![[Internal links#^b15695]]` |
| 图片 | `![[图.jpg]]`；`![[图.jpg|宽]]`（等比缩放）；`![[图.jpg|宽x高]]` |
| 外部图片 | `![宽](URL)`、`![宽x高](URL)` |
| 音频 | `![[音频.ogg]]` |
| PDF | `![[Document.pdf]]`；`![[Document.pdf#page=N]]`（指定页）；`![[Document.pdf#height=400]]`（高度像素） |
| Canvas | `![[My canvas.canvas]]`（限制：仅显示形状，不显示卡片内文本） |
| 列表 | 给列表加 `^my-list-id` 后 `![[My note#^my-list-id]]` |

> source: https://obsidian.md/help/embeds（抓取 2026-06-04）

## 链接维护行为

- **重命名自动更新**：重命名文件时 Obsidian 自动更新全部指向它的内部链接；可在 Settings → Files and links → "Automatically update internal links" 关闭
- 路径解析（重名文件区分、链接格式设置）官方 links 页未展开说明——涉及该行为时不要假设，去 Settings → Files and links 实际确认或查官方文档相关页

> source: https://obsidian.md/help/links（抓取 2026-06-04）

## 链接/文件名非法字符

官方原文："A string which contains the following characters may not work as a link: `# | ^ : %% [[ ]]`"——这些是链接语法保留字符，文件名中应避免，并遵循安全文件名实践。

> source: https://obsidian.md/help/links（抓取 2026-06-04）
