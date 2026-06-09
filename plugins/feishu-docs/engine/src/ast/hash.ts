import { createHash } from "node:crypto";
import { normalizeMarks } from "./normalize";
import type { AstNode, InlineRun } from "./types";

// 确定性序列化：递归按 key 排序，使 props/attrs 的插入顺序不影响 hash
// （程序化构造 AST 时 key 顺序可能不同，但内容等价应得同一 hash）
function stableStringify(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(stableStringify).join(",") + "]";
  const obj = v as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k])).join(",") + "}";
}

function canonRun(r: InlineRun) { return { t: r.text, m: normalizeMarks(r.marks), a: r.attrs ?? {} }; }
function canon(n: AstNode): unknown {
  return { type: n.type, props: n.props, text: n.text.map(canonRun), children: n.children.map(canon) };
}
export function nodeHash(n: AstNode): string {
  return createHash("sha1").update(stableStringify(canon(n))).digest("hex");
}
