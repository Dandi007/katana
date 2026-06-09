import { expect, test } from "bun:test";
import { normalizeMarks } from "./normalize";

test("marks 按固定顺序排列", () => { expect(normalizeMarks(["code", "b", "a"])).toEqual(["a", "b", "code"]); });
test("去重幂等", () => { expect(normalizeMarks(["b", "b", "em"])).toEqual(["b", "em"]); });
test("全集顺序 a>b>em>del>u>code>span", () => {
  expect(normalizeMarks(["span", "u", "del", "em", "b", "a", "code"])).toEqual(["a", "b", "em", "del", "u", "code", "span"]);
});
