import { expect, test } from "bun:test";
import { mkdtemp, readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pull } from "./pull";
import type { Runner } from "../lark/client";

const envelope = readFileSync(new URL("../../fixtures/sample.fetch.json", import.meta.url), "utf8");

// node-get 链：self(docx) → P1(Folder A) → P2(Space Root) → root
function wikiRunner(): Runner {
  const nodes: Record<string, any> = {
    "/wiki/SELF": { obj_token: "PYxgdqFCioL2zYxD5YvcGUllnyd", obj_type: "docx", parent_node_token: "P1", space_id: "S1", title: "Doc", node_token: "SELF" },
    "/wiki/P1": { obj_token: "PA", obj_type: "docx", parent_node_token: "P2", space_id: "S1", title: "Folder A", node_token: "P1" },
    "/wiki/P2": { obj_token: "PR", obj_type: "docx", parent_node_token: "", space_id: "S1", title: "Space Root", node_token: "P2" },
  };
  return async (args) => {
    if (args[1] === "+node-get") {
      const url = args[args.length - 1];
      const key = Object.keys(nodes).find((k) => url.includes(k))!;
      return JSON.stringify({ ok: true, data: nodes[key] });
    }
    return envelope; // docs +fetch
  };
}

test("drive docx：平铺落盘（无子目录）+ md 带 frontmatter", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const res = await pull({ root, docUrl: "https://x.feishu.cn/docx/d" }, async () => envelope);
  if (res.skipped) throw new Error("should not skip");
  expect(res.astPath).not.toContain("/"); // 平铺：文件名里无路径分隔
  const ast = JSON.parse(await readFile(join(root, res.astPath), "utf8"));
  expect(ast.docId).toBe("PYxgdqFCioL2zYxD5YvcGUllnyd");
  expect(ast.location.isWiki).toBe(false);
  const md = await readFile(join(root, res.mdPath), "utf8");
  expect(md.startsWith("---\n")).toBe(true);
  expect(md).toContain('feishu_doc_id: "PYxgdqFCioL2zYxD5YvcGUllnyd"');
  expect(md).toContain('feishu_obj_type: "docx"');
  expect(md).not.toContain("feishu_breadcrumb"); // drive 无祖先
  const idx = JSON.parse(await readFile(join(root, ".index.json"), "utf8"));
  expect(idx.entries[ast.docId].path).toBe(res.astPath);
});

test("wiki docx：frontmatter 带 breadcrumb + parent wikilink，且平铺", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const res = await pull({ root, docUrl: "https://x.feishu.cn/wiki/SELF" }, wikiRunner());
  if (res.skipped) throw new Error("should not skip");
  expect(res.astPath).not.toContain("/");
  const ast = JSON.parse(await readFile(join(root, res.astPath), "utf8"));
  expect(ast.location.breadcrumb.map((b: any) => b.title)).toEqual(["Space Root", "Folder A"]);
  const md = await readFile(join(root, res.mdPath), "utf8");
  expect(md).toContain('feishu_breadcrumb: "Space Root / Folder A"');
  expect(md).toContain('feishu_parent: "[[Folder A-PA]]"');
});

test("非 docx（file/sheet…）优雅跳过，不落盘", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const runner: Runner = async (args) =>
    args[1] === "+node-get"
      ? JSON.stringify({ ok: true, data: { obj_type: "file", title: "x.pdf", parent_node_token: "", obj_token: "F1", space_id: "S1", node_token: "N" } })
      : envelope;
  const res = await pull({ root, docUrl: "https://x.feishu.cn/wiki/FILE" }, runner);
  expect(res.skipped).toBe(true);
  if (res.skipped) expect(res.reason).toContain("file");
});
