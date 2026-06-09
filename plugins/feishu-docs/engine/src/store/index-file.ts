import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import type { IndexFile, IndexEntry } from "../ast/types";

export async function loadIndex(path: string): Promise<IndexFile> {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (err: any) {
    // 仅"文件不存在"才视为空 index；权限/损坏/磁盘错必须上抛，
    // 否则 upsert 会用空 index 覆盖真实数据（数据丢失）
    if (err?.code === "ENOENT") return { version: 1, entries: {} };
    throw err;
  }
}

export async function upsertEntry(path: string, e: IndexEntry): Promise<void> {
  const idx = await loadIndex(path);
  idx.entries[e.docId] = e;
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(idx, null, 2) + "\n");
}
