import { expect, test } from "bun:test";
import { resolveLocation } from "./locate";
import type { Runner } from "./client";

test("drive /docx/ URL：不调 node-get，无 breadcrumb", async () => {
  let calls = 0;
  const run: Runner = async () => { calls++; return "{}"; };
  const loc = await resolveLocation("https://x.feishu.cn/docx/abc", run);
  expect(calls).toBe(0);
  expect(loc.isWiki).toBe(false);
  expect(loc.objType).toBe("docx");
  expect(loc.breadcrumb).toEqual([]);
});

test("wiki URL：走 parent 链构建 root→parent 顺序的 breadcrumb", async () => {
  const nodes: Record<string, any> = {
    "/wiki/SELF": { obj_token: "D0", obj_type: "docx", parent_node_token: "P1", space_id: "SP", title: "Self", node_token: "SELF" },
    "/wiki/P1": { obj_token: "D1", obj_type: "docx", parent_node_token: "P2", space_id: "SP", title: "Mid", node_token: "P1" },
    "/wiki/P2": { obj_token: "D2", obj_type: "docx", parent_node_token: "", space_id: "SP", title: "Top", node_token: "P2" },
  };
  const run: Runner = async (args) => {
    const url = args[args.length - 1];
    const key = Object.keys(nodes).find((k) => url.includes(k))!;
    return JSON.stringify({ ok: true, data: nodes[key] });
  };
  const loc = await resolveLocation("https://x.feishu.cn/wiki/SELF", run);
  expect(loc.isWiki).toBe(true);
  expect(loc.spaceId).toBe("SP");
  expect(loc.breadcrumb.map((b) => b.title)).toEqual(["Top", "Mid"]);
  expect(loc.breadcrumb.map((b) => b.docId)).toEqual(["D2", "D1"]);
});

test("wiki 根节点（parent 为空）：breadcrumb 为空", async () => {
  const run: Runner = async () =>
    JSON.stringify({ ok: true, data: { obj_token: "D", obj_type: "docx", parent_node_token: "", space_id: "SP", title: "Root", node_token: "N" } });
  const loc = await resolveLocation("https://x.feishu.cn/wiki/ROOT", run);
  expect(loc.breadcrumb).toEqual([]);
  expect(loc.objType).toBe("docx");
});

test("wiki 非 docx 节点：objType 透传（供 pull 跳过）", async () => {
  const run: Runner = async () =>
    JSON.stringify({ ok: true, data: { obj_token: "F", obj_type: "file", parent_node_token: "", space_id: "SP", title: "x.pdf", node_token: "N" } });
  const loc = await resolveLocation("https://x.feishu.cn/wiki/FILE", run);
  expect(loc.objType).toBe("file");
});
