export type Runner = (args: string[]) => Promise<string>;
export interface FetchedDoc { content: string; documentId: string; revisionId: number; }

const defaultRunner: Runner = async (args) => {
  const proc = Bun.spawn(["lark-cli", ...args], { stdout: "pipe", stderr: "pipe" });
  const out = await new Response(proc.stdout).text();
  if ((await proc.exited) !== 0) throw new Error(`lark-cli failed: ${await new Response(proc.stderr).text()}`);
  return out;
};

export async function fetchDoc(docUrlOrToken: string, run: Runner = defaultRunner): Promise<FetchedDoc> {
  const out = await run(["docs", "+fetch", "--api-version", "v2", "--detail", "with-ids", "--doc", docUrlOrToken]);
  const env = JSON.parse(out);
  if (!env.ok) throw new Error(`fetch not ok: ${out.slice(0, 200)}`);
  const d = env.data.document;
  return { content: d.content, documentId: d.document_id, revisionId: d.revision_id };
}
