import { expect, test } from "bun:test";
import { renderFrontmatter } from "./frontmatter";
import type { DocModel } from "../ast/types";

const base = (over: Partial<DocModel> = {}): DocModel => ({
  docId: "D0", feishuDocToken: "D0", title: "我的文档", root: [], ...over,
});

test("无 location：最小 frontmatter（title + doc_id）", () => {
  const fm = renderFrontmatter(base());
  expect(fm.startsWith("---\n")).toBe(true);
  expect(fm).toContain('title: "我的文档"');
  expect(fm).toContain('feishu_doc_id: "D0"');
  expect(fm).not.toContain("feishu_breadcrumb");
  expect(fm.trimEnd().endsWith("---")).toBe(true);
});

test("wiki location：breadcrumb + parent wikilink（指向直接父）", () => {
  const fm = renderFrontmatter(base({
    location: {
      url: "https://x.feishu.cn/wiki/SELF", objType: "docx", isWiki: true, spaceId: "SP",
      breadcrumb: [
        { title: "Top", docId: "D2", nodeToken: "P2" },
        { title: "Mid 组", docId: "D1", nodeToken: "P1" },
      ],
    },
  }));
  expect(fm).toContain('feishu_space_id: "SP"');
  expect(fm).toContain('feishu_breadcrumb: "Top / Mid 组"');
  expect(fm).toContain('feishu_parent: "[[Mid 组-D1]]"'); // 直接父 = breadcrumb 末位
});

test("标题含 YAML 特殊字符被转义", () => {
  const fm = renderFrontmatter(base({ title: 'A: "B" \\ C' }));
  expect(fm).toContain('title: "A: \\"B\\" \\\\ C"');
});
