import { expect, test } from "bun:test";
import { renderMd } from "./ast-to-md";
import type { AstNode } from "../ast/types";

const node = (o: Partial<AstNode>): AstNode => ({ id: "n", feishuBlockId: "b", type: "text", props: {}, text: [], children: [], localHash: "", feishuSyncedHash: "", ...o });

test("飞书 h1/heading1 降级为 ##（frontmatter title 才是唯一 H1）", () => {
  expect(renderMd([node({ type: "heading1", text: [{ text: "标题", marks: [] }] })])).toContain("## 标题");
  expect(renderMd([node({ type: "h1", text: [{ text: "标题", marks: [] }] })])).toContain("## 标题");
});
test("h2→###，h6 封顶不超过 ######", () => {
  expect(renderMd([node({ type: "h2", text: [{ text: "x", marks: [] }] })])).toContain("### x");
  expect(renderMd([node({ type: "h6", text: [{ text: "x", marks: [] }] })])).toContain("###### x");
});
test("MD025：多个飞书 h1 渲染后无 body 顶级 H1（无独立 `# `）", () => {
  const md = renderMd([
    node({ type: "h1", text: [{ text: "A", marks: [] }] }),
    node({ type: "h1", text: [{ text: "B", marks: [] }] }),
  ]);
  // 任何行都不应是单 # 的 ATX H1
  expect(md.split("\n").some((l) => /^#\s/.test(l))).toBe(false);
});
test("加粗 run 渲染成 **", () => {
  expect(renderMd([node({ text: [{ text: "粗", marks: ["b"] }] })])).toContain("**粗**");
});
test("资源块降级成带 token 的占位注释", () => {
  const md = renderMd([node({ type: "sheet", props: { token: "shtxxx" } })]);
  expect(md).toMatch(/sheet/i);
  expect(md).toContain("shtxxx");
});
test("容器(callout)渲染其子块", () => {
  const md = renderMd([node({ type: "callout", children: [node({ text: [{ text: "inner", marks: [] }] })] })]);
  expect(md).toContain("inner");
});
