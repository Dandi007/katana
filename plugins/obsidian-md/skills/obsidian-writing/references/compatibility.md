# Obsidian Flavored Markdown 与兼容性

> 本文件全部规则提炼自 Obsidian 官方文档（obsidian.md/help），每节末尾标注出处。官方未覆盖的行为不要假设——存疑时 WebFetch 出处 URL 重新确认。

## 方言构成

Obsidian Flavored Markdown 兼容三个规范：**CommonMark + GitHub Flavored Markdown (GFM) + LaTeX**。官方定位是 "strives for maximum capability without breaking any existing formats"。

> source: https://obsidian.md/help/obsidian-flavored-markdown（抓取 2026-06-04）

## 支持的扩展语法清单（官方页面表格）

| 语法 | 功能 |
|------|------|
| `[[Link]]` | 内部链接 |
| `![[Link]]` | 嵌入文件 |
| `![[Link#^id]]` | 嵌入块 |
| `^id` | 定义块标识符 |
| `[^id]` | 脚注 |
| `%%Text%%` | 注释 |
| `~~Text~~` | 删除线 |
| `==Text==` | 高亮 |
| ```` ``` ```` | 代码块 |
| `- [ ]` / `- [x]` | 任务列表 |
| `> [!note]` | Callout |
| 表格语法 | 表格 |

> source: https://obsidian.md/help/obsidian-flavored-markdown（抓取 2026-06-04）

## 与标准 Markdown 的行为差异

- **HTML 元素内部不解析 Markdown**：`<div>**bold**</div>` 中的 `**` 不会生效。官方解释为 "performance optimization and to keep parser complexity low"，刻意设计。需要在 HTML 内格式化时使用 HTML 标签本身（如 `<strong>`）
- 默认宽松换行（单 Enter 即显示换行）与标准 Markdown 不同，详见 references/formatting.md「换行与段落」节及 "Strict line breaks" 设置

> source: https://obsidian.md/help/obsidian-flavored-markdown + https://obsidian.md/help/syntax（抓取 2026-06-04）

## 跨工具可移植性提示

上表中各语法的标准归属（用于判断「导出到 Obsidian 以外的渲染器会怎样」）：

- **CommonMark 通用**：代码块、脚注以外的基础语法
- **GFM 渲染器（如 GitHub）可支持**：删除线、任务列表、表格、脚注
- **Obsidian 专有，标准渲染器会原样显示文本**：`[[wikilink]]`、`![[嵌入]]`、`^块标识符`、`%%注释%%`、`==高亮==`、`> [!type]` callout（在标准渲染器中退化为普通 blockquote 文本）

注：归属判断为编者按 CommonMark/GFM 规范对官方清单的分类，OFM 页面本身未给出此对照；存疑时以目标渲染器实测为准。

> source: https://obsidian.md/help/obsidian-flavored-markdown（抓取 2026-06-04）+ 编者分类（credibility: medium）
