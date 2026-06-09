export function titleSlug(title: string): string {
  return title
    .replace(/[\/\\:*?"<>|]+/g, "-")   // replace illegal chars with -
    .replace(/\s*-\s*/g, "-")          // collapse spaces around dashes
    .replace(/-{2,}/g, "-")            // collapse consecutive dashes
    .replace(/^-+|-+$/g, "")           // trim leading/trailing dashes
    .trim();                           // strip any residual leading/trailing whitespace
}

export function docFilenames(title: string, docId: string): { md: string; ast: string } {
  // 空标题（无标题文档）兜底为 "untitled"，避免文件名以 "-" 开头
  const slug = titleSlug(title) || "untitled";
  const base = `${slug}-${docId}`;
  return { md: `${base}.md`, ast: `${base}.ast.json` };
}
