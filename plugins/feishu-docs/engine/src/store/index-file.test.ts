import { expect, test } from "bun:test";
import { mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadIndex, upsertEntry } from "./index-file";

test("loadIndex 文件不存在时返回空 IndexFile", async () => {
  const idx = await loadIndex("/nonexistent/path/.index.json");
  expect(idx.version).toBe(1);
  expect(idx.entries).toEqual({});
});

test("upsertEntry 写入后 round-trip 正确", async () => {
  const dir = await mkdtemp(join(tmpdir(), "feishu-idx-"));
  const path = join(dir, ".index.json");
  await upsertEntry(path, { docId: "docxAbc", path: "some-doc-docxAbc.md", title: "Some Doc", feishuDocToken: "tokAbc" });
  const idx = await loadIndex(path);
  expect(idx.entries["docxAbc"].path).toBe("some-doc-docxAbc.md");
  expect(idx.entries["docxAbc"].title).toBe("Some Doc");
});

test("upsertEntry 二次 upsert 更新而非重复", async () => {
  const dir = await mkdtemp(join(tmpdir(), "feishu-idx-"));
  const path = join(dir, ".index.json");
  await upsertEntry(path, { docId: "docxAbc", path: "v1.md", title: "V1", feishuDocToken: "tok1" });
  await upsertEntry(path, { docId: "docxAbc", path: "v2.md", title: "V2", feishuDocToken: "tok2" });
  const idx = await loadIndex(path);
  expect(Object.keys(idx.entries)).toHaveLength(1);
  expect(idx.entries["docxAbc"].path).toBe("v2.md");
});
