import { expect, test } from "bun:test";
import { mkdtemp, readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pull } from "./pull";
import type { Runner } from "../lark/client";

const envelope = readFileSync(new URL("../../fixtures/sample.fetch.json", import.meta.url), "utf8");
const MD_BODY = "# 一级标题\n\n正文 **粗** 段。\n\n\n\n多空行上面。\n";
const mdEnvelope = JSON.stringify({ ok: true, data: { document: { content: MD_BODY } } });

function isMarkdownFetch(args: string[]): boolean {
  return args.includes("--doc-format") && args.includes("markdown");
}

// 区分三类调用：wiki +node-get / docs +fetch markdown / docs +fetch DocxXML
function makeRunner(nodes?: Record<string, any>): Runner {
  return async (args) => {
    if (args[1] === "+node-get") {
      const url = args[args.length - 1];
      const key = nodes ? Object.keys(nodes).find((k) => url.includes(k)) : undefined;
      return JSON.stringify({ ok: true, data: key ? nodes![key] : { obj_type: "docx", parent_node_token: "", title: "x", obj_token: "o", space_id: "S", node_token: "n" } });
    }
    if (isMarkdownFetch(args)) return mdEnvelope;
    return envelope; // DocxXML
  };
}

test("md 视图来自飞书官方 markdown 导出 + frontmatter，且 tidy（无 3+ 空行）", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const res = await pull({ root, docUrl: "https://x.feishu.cn/docx/d" }, makeRunner());
  if (res.skipped) throw new Error("should not skip");
  const md = await readFile(join(root, res.mdPath), "utf8");
  expect(md.startsWith("---\n")).toBe(true);         // frontmatter
  expect(md).toContain("正文 **粗** 段。");           // 飞书官方 md 正文
  expect(md).not.toMatch(/\n{3,}/);                  // 多空行已折叠（MD012）
  // AST 仍来自 DocxXML（SSoT 不变）
  const ast = JSON.parse(await readFile(join(root, res.astPath), "utf8"));
  expect(ast.docId).toBe("PYxgdqFCioL2zYxD5YvcGUllnyd");
});

test("pull 在镜像根写 .markdownlint.json", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  await pull({ root, docUrl: "https://x.feishu.cn/docx/d" }, makeRunner());
  const cfg = JSON.parse(await readFile(join(root, ".markdownlint.json"), "utf8"));
  expect(cfg.MD033).toBe(false);
  expect(cfg.MD013).toBe(false);
});

test("wiki docx：frontmatter 带 breadcrumb + parent wikilink，平铺", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const nodes = {
    "/wiki/SELF": { obj_token: "PYxgdqFCioL2zYxD5YvcGUllnyd", obj_type: "docx", parent_node_token: "P1", space_id: "S1", title: "Doc", node_token: "SELF" },
    "/wiki/P1": { obj_token: "PA", obj_type: "docx", parent_node_token: "", space_id: "S1", title: "Folder A", node_token: "P1" },
  };
  const res = await pull({ root, docUrl: "https://x.feishu.cn/wiki/SELF" }, makeRunner(nodes));
  if (res.skipped) throw new Error("should not skip");
  expect(res.astPath).not.toContain("/");
  const md = await readFile(join(root, res.mdPath), "utf8");
  expect(md).toContain('feishu_breadcrumb: "Folder A"');
  expect(md).toContain('feishu_parent: "[[Folder A-PA]]"');
});

test("非 docx 优雅跳过，不落盘", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const runner: Runner = async (args) =>
    args[1] === "+node-get"
      ? JSON.stringify({ ok: true, data: { obj_type: "file", title: "x.pdf", parent_node_token: "", obj_token: "F", space_id: "S", node_token: "N" } })
      : envelope;
  const res = await pull({ root, docUrl: "https://x.feishu.cn/wiki/FILE" }, runner);
  expect(res.skipped).toBe(true);
});
