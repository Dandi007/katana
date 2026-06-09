import { createHash } from "node:crypto";
import { normalizeMarks } from "./normalize";
import type { AstNode, InlineRun } from "./types";

function canonRun(r: InlineRun) { return { t: r.text, m: normalizeMarks(r.marks), a: r.attrs ?? {} }; }
function canon(n: AstNode): unknown {
  return { type: n.type, props: n.props, text: n.text.map(canonRun), children: n.children.map(canon) };
}
export function nodeHash(n: AstNode): string {
  return createHash("sha1").update(JSON.stringify(canon(n))).digest("hex");
}
