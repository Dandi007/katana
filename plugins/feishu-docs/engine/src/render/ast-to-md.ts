import type { AstNode, InlineRun } from "../ast/types";

function inline(runs: InlineRun[]): string {
  return runs.map((r) => {
    let t = r.text;
    if (r.marks.includes("code")) t = "`" + t + "`";
    if (r.marks.includes("b")) t = `**${t}**`;
    if (r.marks.includes("em")) t = `*${t}*`;
    if (r.marks.includes("del")) t = `~~${t}~~`;
    return t;
  }).join("");
}

function headingLevel(type: string): number | null {
  const m = type.match(/^(?:h|heading)(\d)$/);
  return m ? Number(m[1]) : null;
}

function block(n: AstNode): string {
  const lvl = headingLevel(n.type);
  // 降一级：frontmatter 的 title 才是文档唯一 H1；飞书 body h1→## …，
  // 避免一篇里多个 `# ` 触发 markdownlint MD025（single-h1）。封顶 h6。
  if (lvl) return `${"#".repeat(Math.min(lvl + 1, 6))} ${inline(n.text)}`;
  if (["sheet", "bitable", "whiteboard"].includes(n.type))
    return `<!-- ${n.type} token=${n.props.token ?? ""} (只读视图，详见飞书) -->`;
  const self = inline(n.text);
  const kids = n.children.map(block).join("\n\n");
  return [self, kids].filter(Boolean).join("\n\n");
}

export function renderMd(root: AstNode[]): string {
  return root.map(block).join("\n\n") + "\n";
}
