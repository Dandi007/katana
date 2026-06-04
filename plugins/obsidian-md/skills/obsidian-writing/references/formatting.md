# 标题与格式语法

> 本文件全部规则提炼自 Obsidian 官方文档（obsidian.md/help），每节末尾标注出处。官方未覆盖的行为不要假设——存疑时 WebFetch 出处 URL 重新确认。

## 标题（Heading）

- 行首 1–6 个 `#` 创建对应级别标题（官方原文 "add up to six `#` symbols before your heading text"）
- **`#` 与文字之间写一个空格**：官方所有示例均为 `# This is a heading 1` 形式；且正文中 `#` 紧跟文字会被解析为 **tag**（见 tags 页规则），所以 `#标题` 不是标题而是 tag
- 标题文本即 `[[Note#Heading]]` 锚点目标（见 references/links.md），同文件内标题应避免重复，改标题名等于改锚点

> source: https://obsidian.md/help/syntax + https://obsidian.md/help/tags（抓取 2026-06-04）

## 换行与段落

- 默认（非 strict）：单次 Enter 产生新行，但视为**同一段落的续行**（官方原文 "treated as a *continuation* of the same paragraph"）
- 段落内强制换行：行尾加**两个空格**再 Enter，或 Shift+Enter
- 独立段落：用**空行**分隔文本块
- "Strict line breaks" 设置开启后遵循标准 Markdown：无尾随空格的单换行会合并为一行；两个以上尾随空格产生 `<br>`；双换行产生独立 `<p>` 段落
- Reading view / Publish 中多个相邻空格折叠为单个空格

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）

## 文本样式

| 样式 | 语法 | 备注 |
|------|------|------|
| 粗体 | `**text**` 或 `__text__` | |
| 斜体 | `*text*` 或 `_text_` | |
| 粗斜体 | `***text***` 或 `___text___` | 嵌套写法 `**Bold __italic__**` |
| 删除线 | `~~text~~` | GFM 语法 |
| 高亮 | `==text==` | **Obsidian 特有**，标准 Markdown 不存在 |

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）

## 列表与任务列表

- 无序列表：`-`、`*` 或 `+` 开头
- 有序列表：数字加 `.` 或 `)`（`1.` 或 `1)`）
- 任务列表：`- [ ]`（未完成）/ `- [x]`（完成）；官方注明括号内可用任意字符
- 嵌套：缩进实现，可混合不同列表类型；编辑时 Tab / Shift+Tab 调整层级

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）

## 引用与 Callout

- 引用：行首 `>`；引用块首行加 `[!type]` 即变为 callout
- Callout 语法：`> [!type]`，后续行继续以 `>` 开头
- 自定义标题：`> [!tip] 自定义标题`；可折叠：`> [!type]+`（默认展开）/ `> [!type]-`（默认折叠）
- 嵌套 callout：增加 `>` 层数
- 类型标识符**不区分大小写**；不支持的类型默认按 `note` 渲染
- 受支持类型及别名：

  | 类型 | 别名 |
  |------|------|
  | note | — |
  | abstract | summary, tldr |
  | info | — |
  | todo | — |
  | tip | hint, important |
  | success | check, done |
  | question | help, faq |
  | warning | caution, attention |
  | failure | fail, missing |
  | danger | error |
  | bug | — |
  | example | — |
  | quote | cite |

- 自定义类型：CSS snippet 中 `.callout[data-callout="custom-type"]` 配 `--callout-color`（RGB 数值）与 `--callout-icon`（Lucide 图标 ID 或 SVG）

> source: https://obsidian.md/help/syntax + https://obsidian.md/help/callouts（抓取 2026-06-04）

## 代码

- 行内代码：单反引号；要在行内代码中包含反引号时用双反引号包裹
- 代码块：三个及以上反引号或波浪号围栏，可加语言标签（如 ```` ```js ````）获得语法高亮（Prism）
- 嵌套代码块：外层使用更多围栏符号

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）

## 表格

- `|` 分隔列，表头分隔行用连字符，**每列至少两个连字符**
- 两侧外围竖线可选但建议保留
- 对齐：左 `:--`、居中 `:--:`、右 `--:`
- 单元格内支持基础格式（链接、嵌入等）；**单元格内使用竖线必须转义 `\|`**（wikilink 别名在表格内即需 `[[Note\|别名]]`）

> source: https://obsidian.md/help/advanced-syntax（抓取 2026-06-04）

## 数学公式与图表

- 行内公式：`$...$`；块级公式：`$$...$$`（LaTeX/MathJax 语法）
- Mermaid 图表：```` ```mermaid ```` 代码块；节点加 `internal-link` class 可链接到笔记；含特殊字符的文本用双引号包裹

> source: https://obsidian.md/help/advanced-syntax（抓取 2026-06-04）

## 脚注与注释

- 脚注：`[^id]` 引用 + `[^id]: 内容` 定义
- **行内脚注仅在 Reading view 生效**（官方原文 "Inline footnotes only work in reading view, not in Live Preview"）
- 注释：`%%注释内容%%`，仅编辑视图可见，阅读视图/导出不显示

> source: https://obsidian.md/help/syntax + https://obsidian.md/help/obsidian-flavored-markdown（抓取 2026-06-04）

## 转义字符

- 反斜杠 `\` 转义特殊字符：`\*`、`\_`、`\#`、`` \` ``、`\|`、`\~` 等
- 行首数字加句点要避免被解析为有序列表时，转义句点：`1\.`（不是 `\1.`）

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）

## 图片尺寸

- 语法：在链接目标后加 `|640x480`（宽x高）或 `|640`（仅宽度，按原比例缩放）
- 外部图片：`![Engelbart|100x145](url)`；vault 内图片：`![[图.png|100]]`（见 references/links.md 嵌入节）

> source: https://obsidian.md/help/syntax + https://obsidian.md/help/embeds（抓取 2026-06-04）

## 分隔线

- `***`、`---` 或 `___`：三个及以上，符号间可有空格

> source: https://obsidian.md/help/syntax（抓取 2026-06-04）
