import type { InlineMark } from "./types";

const ORDER: InlineMark[] = ["a", "b", "em", "del", "u", "code", "span"];

export function normalizeMarks(marks: InlineMark[]): InlineMark[] {
  const set = new Set(marks);
  return ORDER.filter((m) => set.has(m));
}
