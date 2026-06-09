import { expect, test } from "bun:test";
import { titleSlug, docFilenames } from "./layout";

test("标题 slug 清洗非法文件名字符", () => { expect(titleSlug("A/B: C*?")).toBe("A-B-C"); });
test("文件名 = <slug>-<docid>.{md,ast.json}", () => {
  const f = docFilenames("我的文档/标题", "docxAbc123");
  expect(f.md).toBe("我的文档-标题-docxAbc123.md");
  expect(f.ast).toBe("我的文档-标题-docxAbc123.ast.json");
});
