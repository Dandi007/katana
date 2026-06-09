import { defaultRunner, type Runner } from "./client";
import type { FeishuLocation, FeishuBreadcrumbNode } from "../ast/types";

// 解析飞书文档在飞书里的"位置"，用于把 layout 信息以 frontmatter 链接图保留（非文件夹）。
// - wiki 文档：node-get 自身 + 向上走 parent_node_token 链得到祖先 breadcrumb。
// - drive 文档（/docx/...）：飞书 API 无父文件夹查询，breadcrumb 为空。

interface WikiNode {
  title: string;
  objToken: string;       // 底层文档 id（docx 时即 docId）
  objType: string;
  parentNodeToken: string;
  spaceId: string;
  nodeToken: string;
}

function host(url: string): string {
  try { return new URL(url).host; } catch { return "feishu.cn"; }
}

function isWikiUrl(url: string): boolean { return /\/wiki\//.test(url); }

async function nodeGet(wikiUrl: string, run: Runner): Promise<WikiNode | null> {
  // 非致命：节点无读权限（131006）等会让 lark-cli 非零退出（run 抛错），
  // 或返回 ok:false。任一情况都返回 null，让调用方截断祖先链而非整体失败——
  // 文档本身可读时，某个跨空间祖先不可读不应阻断 pull。
  let env: any;
  try {
    env = JSON.parse(await run(["wiki", "+node-get", "--node-token", wikiUrl]));
  } catch { return null; }
  if (!env?.ok) return null;
  const d = env.data;
  return {
    title: d.title ?? "",
    objToken: d.obj_token ?? "",
    objType: d.obj_type ?? "",
    parentNodeToken: d.parent_node_token ?? "",
    spaceId: d.space_id ?? "",
    nodeToken: d.node_token ?? "",
  };
}

const MAX_DEPTH = 32; // 防御：祖先链异常时不无限走

export async function resolveLocation(url: string, run: Runner = defaultRunner): Promise<FeishuLocation> {
  if (!isWikiUrl(url)) {
    // drive 文档：无父文件夹可查
    return { url, objType: "docx", isWiki: false, breadcrumb: [] };
  }

  const self = await nodeGet(url, run);
  if (!self) return { url, objType: "", isWiki: true, breadcrumb: [] };

  const h = host(url);
  const breadcrumb: FeishuBreadcrumbNode[] = [];
  let parent = self.parentNodeToken;
  for (let i = 0; parent && i < MAX_DEPTH; i++) {
    const node = await nodeGet(`https://${h}/wiki/${parent}`, run);
    if (!node) break;
    breadcrumb.unshift({ title: node.title, docId: node.objToken, nodeToken: node.nodeToken });
    parent = node.parentNodeToken;
  }

  return { url, objType: self.objType, isWiki: true, spaceId: self.spaceId, breadcrumb };
}
