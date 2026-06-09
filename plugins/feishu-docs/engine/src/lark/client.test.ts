import { expect, test } from "bun:test";
import { fetchDoc } from "./client";

const envelope = JSON.stringify({ ok: true, identity: "user",
  data: { document: { content: "<title id=\"d\">T</title><p id=\"b1\">x</p>", document_id: "d", revision_id: 3 } } });

test("fetchDoc 带 --api-version v2 --detail with-ids 并拆出 content/documentId/revisionId", async () => {
  let cmd = "";
  const fakeRun = async (args: string[]) => { cmd = args.join(" "); return envelope; };
  const r = await fetchDoc("https://x.feishu.cn/docx/d", fakeRun);
  expect(cmd).toContain("docs +fetch");
  expect(cmd).toContain("--api-version v2");
  expect(cmd).toContain("--detail with-ids");
  expect(r.documentId).toBe("d");
  expect(r.revisionId).toBe(3);
  expect(r.content).toContain("<title id=\"d\">");
});
test("ok:false 抛错", async () => {
  await expect(fetchDoc("x", async () => JSON.stringify({ ok: false }))).rejects.toThrow();
});
