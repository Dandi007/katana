import { execFile } from "node:child_process";
import { promisify } from "node:util";

export type Runner = (args: string[]) => Promise<string>;
export interface FetchedDoc { content: string; documentId: string; revisionId: number; }

const execFileAsync = promisify(execFile);

// 用 node:child_process（node 与 bun 都支持），不用 Bun.spawn——
// 引擎以 `bun build --target node` 分发给 node≥18 运行，Bun 全局在 node 下不存在。
// execFile 在非零退出时自动 reject；maxBuffer 调大以容纳大文档 content。
export const defaultRunner: Runner = async (args) => {
  const { stdout } = await execFileAsync("lark-cli", args, { maxBuffer: 64 * 1024 * 1024 });
  return stdout;
};

export async function fetchDoc(docUrlOrToken: string, run: Runner = defaultRunner): Promise<FetchedDoc> {
  const out = await run(["docs", "+fetch", "--api-version", "v2", "--detail", "with-ids", "--doc", docUrlOrToken]);
  const env = JSON.parse(out);
  if (!env.ok) throw new Error(`fetch not ok: ${out.slice(0, 200)}`);
  const d = env.data.document;
  return { content: d.content, documentId: d.document_id, revisionId: d.revision_id };
}

// 飞书官方 Markdown 导出，作为只读 .md 视图（结构远好于手写渲染：正经代码围栏/表格/列表）。
export async function fetchMarkdown(docUrlOrToken: string, run: Runner = defaultRunner): Promise<string> {
  const out = await run(["docs", "+fetch", "--api-version", "v2", "--doc-format", "markdown", "--doc", docUrlOrToken]);
  const env = JSON.parse(out);
  if (!env.ok) throw new Error(`md fetch not ok: ${out.slice(0, 200)}`);
  return env.data.document.content ?? "";
}
