import { expect, test } from "bun:test";
import { mkdtemp, readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pull } from "./pull";

const envelope = readFileSync(new URL("../../fixtures/sample.fetch.json", import.meta.url), "utf8");

test("pull 落盘 ast.json + md，并写 .index.json（key=docId）", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const runner = async () => envelope;
  const res = await pull({ root, docUrl: "https://x.feishu.cn/docx/d", relPath: "团队/规划" }, runner);
  const ast = JSON.parse(await readFile(join(root, res.astPath), "utf8"));
  expect(ast.docId).toBe("PYxgdqFCioL2zYxD5YvcGUllnyd");
  expect(ast.root.length).toBeGreaterThan(0);
  expect((await readFile(join(root, res.mdPath), "utf8")).length).toBeGreaterThan(0);
  const idx = JSON.parse(await readFile(join(root, ".index.json"), "utf8"));
  expect(idx.entries[ast.docId].path).toBe(res.astPath);
});

test("relPath 为空也能落盘到 root 根", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-"));
  const res = await pull({ root, docUrl: "x", relPath: "" }, async () => envelope);
  expect(res.astPath).toContain("PYxgdqFCioL2zYxD5YvcGUllnyd");
  await readFile(join(root, res.astPath), "utf8"); // must exist, no throw
});
