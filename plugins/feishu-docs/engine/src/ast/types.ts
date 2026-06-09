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

export interface DocModel {
  docId: string;                    // canonical key
  feishuDocToken: string;
  title: string;
  root: AstNode[];
}

export interface IndexEntry { docId: string; path: string; title: string; feishuDocToken: string; }
export interface IndexFile { version: 1; entries: Record<string, IndexEntry>; }
