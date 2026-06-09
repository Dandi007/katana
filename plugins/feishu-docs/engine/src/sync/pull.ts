import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fetchDoc, fetchMarkdown, type Runner } from "../lark/client";
import { resolveLocation } from "../lark/locate";
import { parseContent } from "../parse/docx-to-ast";
import { renderFrontmatter } from "../render/frontmatter";
import { tidyMarkdown, ensureMarkdownlintConfig } from "../render/md-view";
import { docFilenames } from "../store/layout";
import { upsertEntry } from "../store/index-file";

export interface PullOpts { root: string; docUrl: string; }

export type PullResult =
  | { skipped: true; reason: string }
  | { skipped: false; astPath: string; mdPath: string; docId: string };

export async function pull(opts: PullOpts, run?: Runner): Promise<PullResult> {
  // 先解析飞书位置（也得到 obj_type）：非 docx（file/sheet/bitable/mindnote…）优雅跳过
  const location = await resolveLocation(opts.docUrl, run);
  if (location.objType && location.objType !== "docx") {
    return { skipped: true, reason: `unsupported obj_type "${location.objType}"` };
  }

  // AST（SSoT，程序编辑用）← DocxXML（带 block id）
  const fetched = await fetchDoc(opts.docUrl, run);
  const doc = parseContent(fetched.content, fetched.documentId);
  doc.location = location;

  // .md（只读人看）← 飞书官方 markdown 导出（结构远好于手写渲染）
  const mdBody = tidyMarkdown(await fetchMarkdown(opts.docUrl, run));

  // 平铺：直接落在 feishu_docs_root 下，不建子目录——layout 信息进 frontmatter（链接图）
  const { md, ast } = docFilenames(doc.title, doc.docId);
  await mkdir(opts.root, { recursive: true });
  await writeFile(join(opts.root, ast), JSON.stringify(doc, null, 2) + "\n");
  await writeFile(join(opts.root, md), renderFrontmatter(doc) + "\n" + mdBody);
  await upsertEntry(join(opts.root, ".index.json"),
    { docId: doc.docId, path: ast, title: doc.title, feishuDocToken: doc.feishuDocToken });
  await ensureMarkdownlintConfig(opts.root);

  return { skipped: false, astPath: ast, mdPath: md, docId: doc.docId };
}
