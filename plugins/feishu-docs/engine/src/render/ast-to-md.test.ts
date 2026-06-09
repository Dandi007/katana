import { expect, test } from "bun:test";
import { renderMd } from "./ast-to-md";
import type { AstNode } from "../ast/types";

const node = (o: Partial<AstNode>): AstNode => ({ id: "n", feishuBlockId: "b", type: "text", props: {}, text: [], children: [], localHash: "", feishuSyncedHash: "", ...o });

test("heading1 渲染成 #", () => {
  expect(renderMd([node({ type: "heading1", text: [{ text: "标题", marks: [] }] })])).toContain("# 标题");
});
test("h1 标签型也渲染成 #", () => {
  expect(renderMd([node({ type: "h1", text: [{ text: "标题", marks: [] }] })])).toContain("# 标题");
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
