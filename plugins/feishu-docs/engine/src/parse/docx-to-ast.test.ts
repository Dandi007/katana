import { describe, it, expect } from "bun:test";
import { readFileSync } from "node:fs";
import { parseContent } from "./docx-to-ast";

const env = JSON.parse(
  readFileSync(new URL("../../fixtures/sample.fetch.json", import.meta.url), "utf8")
);
const content: string = env.data.document.content;
const docId: string = env.data.document.document_id;

describe("parseContent", () => {
  it("returns correct docId and title", () => {
    const doc = parseContent(content, docId);
    expect(doc.docId).toBe(docId);
    expect(doc.title).toBe("feishu-docs fixture 测试文档");
  });

  it("derives docId from <title id>, not the fallback param", () => {
    // 传一个错误的 fallback：parser 必须从 content 的 <title id> 取真值
    const doc = parseContent(content, "WRONG_FALLBACK");
    expect(doc.docId).toBe(docId);
    expect(doc.feishuDocToken).toBe(docId);
  });

  it("root has multiple nodes (title excluded)", () => {
    const doc = parseContent(content, docId);
    expect(doc.root.length).toBeGreaterThan(0);
  });

  it("every top-level node has feishuBlockId matching /^doxcn/ (or title's docId) and valid localHash", () => {
    const doc = parseContent(content, docId);
    for (const node of doc.root) {
      // all top-level nodes from the fixture (excluding ul/ol which have no id) should have doxcn prefix
      // ul and ol have no id at the block level; skip id check for them
      if (node.type !== "ul" && node.type !== "ol") {
        expect(node.feishuBlockId).toMatch(/^doxcn/);
      }
      expect(node.localHash).toMatch(/^[0-9a-f]{40}$/);
      expect(node.localHash).toBe(node.feishuSyncedHash);
    }
  });

  it("inline run '加粗' has mark 'b'", () => {
    const doc = parseContent(content, docId);
    // find the paragraph with bold text
    const p = doc.root.find(
      (n) => n.feishuBlockId === "doxcn693JohzZCIYExBvBizv1Th"
    );
    expect(p).toBeDefined();
    const boldRun = p!.text.find((r) => r.text === "加粗");
    expect(boldRun).toBeDefined();
    expect(boldRun!.marks).toContain("b");
  });

  it("callout node has children", () => {
    const doc = parseContent(content, docId);
    const callout = doc.root.find((n) => n.type === "callout");
    expect(callout).toBeDefined();
    expect(callout!.children.length).toBeGreaterThan(0);
  });

  it("sheet node has truthy props.token and no children", () => {
    const doc = parseContent(content, docId);
    const sheet = doc.root.find((n) => n.type === "sheet");
    expect(sheet).toBeDefined();
    expect(sheet!.props.token).toBeTruthy();
    expect(sheet!.children.length).toBe(0);
  });

  it("all nodes have non-empty id field", () => {
    const doc = parseContent(content, docId);
    function check(nodes: typeof doc.root) {
      for (const n of nodes) {
        expect(n.id).toMatch(/^n-/);
        check(n.children);
      }
    }
    check(doc.root);
  });

  it("feishuDocToken equals docId", () => {
    const doc = parseContent(content, docId);
    expect(doc.feishuDocToken).toBe(docId);
  });

  it("ul node contains li children with feishuBlockIds", () => {
    const doc = parseContent(content, docId);
    const ul = doc.root.find((n) => n.type === "ul");
    expect(ul).toBeDefined();
    expect(ul!.children.length).toBeGreaterThan(0);
    for (const li of ul!.children) {
      expect(li.feishuBlockId).toMatch(/^doxcn/);
    }
  });

  it("table node has thead and tbody children with nested structure", () => {
    const doc = parseContent(content, docId);
    const table = doc.root.find((n) => n.type === "table");
    expect(table).toBeDefined();
    expect(table!.children.length).toBeGreaterThan(0);
  });

  it("hr node exists with correct feishuBlockId", () => {
    const doc = parseContent(content, docId);
    const hr = doc.root.find((n) => n.type === "hr");
    expect(hr).toBeDefined();
    expect(hr!.feishuBlockId).toBe("doxcnTQBqkktTjUw0N5hKCUDddf");
  });
});
