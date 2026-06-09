import { expect, test } from "bun:test";
import { nodeHash } from "./hash";
import type { AstNode } from "./types";

const base = (over: Partial<AstNode> = {}): AstNode => ({
  id: "n1", feishuBlockId: "blk1", type: "text", props: {},
  text: [{ text: "hi", marks: ["b", "a"] }], children: [], localHash: "", feishuSyncedHash: "", ...over,
});

test("等价 marks 顺序不影响 hash（先归一化）", () => {
  expect(nodeHash(base({ text: [{ text: "hi", marks: ["b", "a"] }] }))).toBe(nodeHash(base({ text: [{ text: "hi", marks: ["a", "b"] }] })));
});
test("内容变化 hash 变化", () => {
  expect(nodeHash(base({ text: [{ text: "hi", marks: [] }] }))).not.toBe(nodeHash(base({ text: [{ text: "bye", marks: [] }] })));
});
test("id/hash 字段不参与 hash（仅内容）", () => {
  expect(nodeHash(base({ id: "x", localHash: "zzz" }))).toBe(nodeHash(base({ id: "y", localHash: "qqq" })));
});
test("children 顺序参与 hash", () => {
  const c1 = base({ id: "c1" }), c2 = base({ id: "c2", text: [{ text: "x", marks: [] }] });
  expect(nodeHash(base({ children: [c1, c2] }))).not.toBe(nodeHash(base({ children: [c2, c1] })));
});
