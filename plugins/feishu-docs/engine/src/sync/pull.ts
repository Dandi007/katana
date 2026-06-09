import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fetchDoc, type Runner } from "../lark/client";
import { parseContent } from "../parse/docx-to-ast";
import { renderMd } from "../render/ast-to-md";
import { docFilenames } from "../store/layout";
import { upsertEntry } from "../store/index-file";

export interface PullOpts { root: string; docUrl: string; relPath: string; }

export async function pull(opts: PullOpts, run?: Runner) {
  const fetched = await fetchDoc(opts.docUrl, run);
  const doc = parseContent(fetched.content, fetched.documentId);
  const { md, ast } = docFilenames(doc.title, doc.docId);

  // relPath may be "" (root-level); join("", "x") → "x" — correct.
  const astPath = join(opts.relPath, ast);
  const mdPath = join(opts.relPath, md);

  await mkdir(join(opts.root, opts.relPath || "."), { recursive: true });
  await writeFile(join(opts.root, astPath), JSON.stringify(doc, null, 2) + "\n");
  await writeFile(join(opts.root, mdPath), renderMd(doc.root));
  await upsertEntry(join(opts.root, ".index.json"),
    { docId: doc.docId, path: astPath, title: doc.title, feishuDocToken: doc.feishuDocToken });

  return { astPath, mdPath };
}
