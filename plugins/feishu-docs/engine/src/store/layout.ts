export function titleSlug(title: string): string {
  return title
    .replace(/[\/\\:*?"<>|]+/g, "-")   // replace illegal chars with -
    .replace(/\s*-\s*/g, "-")          // collapse spaces around dashes
    .replace(/-{2,}/g, "-")            // collapse consecutive dashes
    .replace(/^-+|-+$/g, "");          // trim leading/trailing dashes
}

export function docFilenames(title: string, docId: string): { md: string; ast: string } {
  const base = `${titleSlug(title)}-${docId}`;
  return { md: `${base}.md`, ast: `${base}.ast.json` };
}
