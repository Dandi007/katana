import type { DocModel } from "../ast/types";
import { titleSlug } from "../store/layout";

// YAML 标量安全输出：双引号包裹 + 转义，避免标题里的 : # [ ] 等破坏 frontmatter。
function yamlStr(s: string): string {
  return '"' + s.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

// 把飞书 layout 信息渲染成 Obsidian frontmatter（链接图，而非文件夹）。
export function renderFrontmatter(doc: DocModel): string {
  const loc = doc.location;
  const lines: string[] = ["---"];
  lines.push(`title: ${yamlStr(doc.title)}`);
  lines.push(`feishu_doc_id: ${yamlStr(doc.docId)}`);
  if (loc) {
    lines.push(`feishu_url: ${yamlStr(loc.url)}`);
    lines.push(`feishu_obj_type: ${yamlStr(loc.objType)}`);
    if (loc.spaceId) lines.push(`feishu_space_id: ${yamlStr(loc.spaceId)}`);
    if (loc.breadcrumb.length > 0) {
      const crumbs = loc.breadcrumb.map((b) => b.title).join(" / ");
      lines.push(`feishu_breadcrumb: ${yamlStr(crumbs)}`);
      const parent = loc.breadcrumb[loc.breadcrumb.length - 1];
      const target = `${titleSlug(parent.title) || "untitled"}-${parent.docId}`;
      // parent 用 wikilink，让飞书层级在本地成为可双链的图
      lines.push(`feishu_parent: ${yamlStr(`[[${target}]]`)}`);
    }
  }
  lines.push("---", "");
  return lines.join("\n");
}
