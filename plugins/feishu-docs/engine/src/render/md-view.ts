import { writeFile, access } from "node:fs/promises";
import { join } from "node:path";

// 折叠多空行 + 去行尾空格，让飞书官方 md 更干净（MD012/MD009）。
export function tidyMarkdown(md: string): string {
  const noTrail = md.split("\n").map((l) => l.replace(/[ \t]+$/, "")).join("\n");
  return noTrail.replace(/\n{3,}/g, "\n\n").replace(/\s+$/, "") + "\n";
}

// 镜像目录的 markdownlint 配置：豁免"忠实导入富内容"必然触发的规则。
// 富格式（颜色/callout/表格样式）在 markdown 里只能用 HTML/扩展表达，
// 对只读导入视图套用手写文档的 lint 规则不合适——这些规则关掉。
export const MARKDOWNLINT_CONFIG: Record<string, unknown> = {
  default: true,
  MD013: false, // line-length（导入正文）
  MD033: false, // inline HTML（颜色/callout 等富块在 md 里必然是 HTML）
  MD025: false, // single-h1（frontmatter title + 文档自身标题并存）
  MD041: false, // first-line-h1（frontmatter title 才是标题）
  MD036: false, // emphasis-as-heading（飞书粗体伪标题）
  MD034: false, // bare-urls（正文链接）
  MD060: false, // table-column-style
  MD007: false, // ul-indent（飞书嵌套缩进风格）
  MD030: false, // list-marker-space
  MD031: false, // blanks-around-fences
  MD026: false, // 标题尾标点（中文 ：。）
  MD024: false, // 重复标题（纪要/转录常见）
  MD001: false, // heading-increment（忠实飞书结构）
};

// 在镜像根写一份 markdownlint 配置（已存在则不覆盖，尊重用户自定义）。
export async function ensureMarkdownlintConfig(root: string): Promise<void> {
  const p = join(root, ".markdownlint.json");
  try { await access(p); return; } catch { /* 不存在 → 写入 */ }
  await writeFile(p, JSON.stringify(MARKDOWNLINT_CONFIG, null, 2) + "\n");
}
