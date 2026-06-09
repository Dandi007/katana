import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import type { IndexFile, IndexEntry } from "../ast/types";

export async function loadIndex(path: string): Promise<IndexFile> {
  try { return JSON.parse(await readFile(path, "utf8")); }
  catch { return { version: 1, entries: {} }; }
}

export async function upsertEntry(path: string, e: IndexEntry): Promise<void> {
  const idx = await loadIndex(path);
  idx.entries[e.docId] = e;
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(idx, null, 2) + "\n");
}
