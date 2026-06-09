# Fixtures

## sample.fetch.json

解析器（`src/parse/docx-to-ast.ts`）的 ground truth。

- **来源**：一篇**自建的非敏感测试文档**（不是用户私人/工作文档），专为覆盖解析器要处理的 block 类型而造。
  - 文档：`https://agirobot.feishu.cn/docx/PYxgdqFCioL2zYxD5YvcGUllnyd`，`document_id=PYxgdqFCioL2zYxD5YvcGUllnyd`
  - 种子 XML：见同目录 `seed-doc.xml`（`docs +create` 的输入）
- **捕获命令**（捕获于 2026-06-09）：
  ```bash
  lark-cli docs +fetch --api-version v2 --detail with-ids --doc "<url>" | jq 'del(._notice)'
  ```
- **脱敏**：内容均为自造测试串，无敏感信息，可整份入库。

## 真实输出结构（重要）

`lark-cli docs +fetch --api-version v2 --detail with-ids` 返回 envelope：

```jsonc
{
  "ok": true,
  "identity": "user",
  "data": {
    "document": {
      "content": "<title id=\"<docId>\">...</title><h1 id=\"<blockId>\">...</h1>...",  // DocxXML 字符串
      "document_id": "<docId>",
      "revision_id": 3
    }
  }
}
```

- **`data.document.content` 是一段 DocxXML 字符串**（HTML 子集），不是 JSON block 树。解析器要解析 XML。
- 每个 block 元素带 `id="<blockId>"` 属性 = 飞书 block_id；`<title id>` 的 id = 文档 id（与 `document_id` 一致）。
- 行内样式是嵌套行内标签：`<b>` `<em>` `<code>` `<span text-color="rgb(...)">` 等。
- 资源块：`<sheet id="<blockId>" sheet-id="WtPoQl" token="K2KM...">`。

## ⚠️ `--detail with-ids` 必需

- 该 flag **未列在 `lark-cli docs +fetch --help`**（隐藏 flag），但真实存在且生效，不报错。
- **不带它**：`content` 既没有 block `id` 属性，也会丢部分行内样式（如 `<span text-color>` 直接被抹平成纯文本）。block id 是整个同步设计的命门，故 fetch **必须**带 `--detail with-ids`。
