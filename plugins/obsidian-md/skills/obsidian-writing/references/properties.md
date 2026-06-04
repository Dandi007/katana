# Frontmatter (Properties) 与 Tags

> 本文件全部规则提炼自 Obsidian 官方文档（obsidian.md/help），每节末尾标注出处。官方未覆盖的行为不要假设——存疑时 WebFetch 出处 URL 重新确认。

## 位置与基本格式

- Properties 必须位于**文件最顶部**，用 `---` 包裹（官方原文 "Type `---` at the very beginning of a file"）
- 属性名与值用**冒号 + 空格**分隔（官方原文 "Property names are separated from their values by a colon followed by a space"）
- **属性名在单篇笔记内必须唯一**（官方原文 "each name must be unique within a note"）

> source: https://obsidian.md/help/properties（抓取 2026-06-04）

## 属性类型

| 类型 | 格式要求 |
|------|---------|
| Text | 单行文本 |
| List | 多值，每值一行，`- ` 前缀 |
| Number | 字面数字（整数或小数） |
| Checkbox | `true` / `false` |
| Date | `2020-08-21`（YYYY-MM-DD） |
| Date & time | `2020-08-21T10:30:00` |
| Tags | 列表格式（见下方 tags 节） |

> source: https://obsidian.md/help/properties（抓取 2026-06-04）

## 默认属性

- `tags`：Tags 类型，列表格式——官方要求每个 tag 独占一行、`- ` 前缀（"each tag on its own line preceded by a hyphen (-) and a space"）
- `aliases`：List 类型（见 references/links.md 别名节）
- `cssclasses`：List 类型
- Obsidian Publish 另有 `publish` / `permalink` / `description` / `image` / `cover`

> source: https://obsidian.md/help/properties（抓取 2026-06-04）

## 关键约束

- **属性值里的内部链接必须加引号**：官方原文 "Internal links in text properties must be surrounded with quotes"；列表属性同样要求（"When using Internal links in list properties, surround them with quotes"）：

  ```yaml
  ---
  up: "[[父笔记]]"
  related:
    - "[[笔记A]]"
    - "[[笔记B]]"
  ---
  ```

- **属性值不渲染 Markdown**（官方原文 "Markdown formatting is not rendered in text properties"）
- JSON frontmatter 可被读取，但会被解释并**保存为 YAML**（"the JSON block will be read, interpreted, and saved as YAML"）
- 不支持：嵌套属性、界面批量编辑、属性内 Markdown；属性值应为 "small, atomic bits of information"

> source: https://obsidian.md/help/properties（抓取 2026-06-04）

## Tag 命名规则

- 两种写法：正文 `#tag`；frontmatter `tags` 属性（**必须列表格式**，官方原文 "Tags in YAML should always be formatted as a list"）
- 合法字符：字母、数字、下划线、连字符、`/`（嵌套层级）、Unicode 字符与 emoji
- **必须含至少一个非数字字符**（官方原文 "Tags must contain at least one non-numerical character"）：`#1984` 无效，`#y1984` 有效
- **不能含空格**（"Tags can't contain blank spaces"）——多词用 camelCase / snake_case / kebab-case
- 嵌套 tag：`#inbox/to-read`；搜索父级会匹配全部子级
- 大小写不敏感（`#tag` 与 `#TAG` 视为同一个），显示时采用首次创建的大小写

> source: https://obsidian.md/help/tags（抓取 2026-06-04）
