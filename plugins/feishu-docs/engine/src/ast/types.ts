export type InlineMark = "a" | "b" | "em" | "del" | "u" | "code" | "span";
export interface InlineRun { text: string; marks: InlineMark[]; attrs?: Record<string, string>; }

export interface AstNode {
  id: string;                       // 本地稳定 ID（永不变）
  feishuBlockId: string;            // 飞书 block 映射
  type: string;                     // 对齐飞书 block type / 标签名
  props: Record<string, unknown>;
  text: InlineRun[];
  children: AstNode[];
  localHash: string;
  feishuSyncedHash: string;
}

// 飞书位置元数据：以"链接图 + breadcrumb"保留 layout，而非文件夹
export interface FeishuBreadcrumbNode { title: string; docId: string; nodeToken: string; }
export interface FeishuLocation {
  url: string;                      // 原始飞书 URL
  objType: string;                  // docx | file | sheet | bitable | mindnote | ...
  isWiki: boolean;
  spaceId?: string;                 // wiki 空间 id（drive 文档无）
  breadcrumb: FeishuBreadcrumbNode[]; // root → ... → 直接父节点（不含自身）；drive 文档为空
}

export interface DocModel {
  docId: string;                    // canonical key
  feishuDocToken: string;
  title: string;
  root: AstNode[];
  location?: FeishuLocation;        // 飞书位置（pull 时解析填充）
}

export interface IndexEntry { docId: string; path: string; title: string; feishuDocToken: string; }
export interface IndexFile { version: 1; entries: Record<string, IndexEntry>; }
