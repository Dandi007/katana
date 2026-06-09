import { expect, test } from "bun:test";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { tidyMarkdown, ensureMarkdownlintConfig, MARKDOWNLINT_CONFIG } from "./md-view";

test("tidyMarkdown 折叠 3+ 空行为 1，去行尾空格，单尾换行", () => {
  const out = tidyMarkdown("a   \n\n\n\nb  \n");
  expect(out).toBe("a\n\nb\n");
});

test("MARKDOWNLINT_CONFIG 豁免富内容必然触发的规则", () => {
  expect(MARKDOWNLINT_CONFIG.MD033).toBe(false);
  expect(MARKDOWNLINT_CONFIG.MD013).toBe(false);
  expect(MARKDOWNLINT_CONFIG.MD025).toBe(false);
});

test("ensureMarkdownlintConfig 不存在则写入，存在则不覆盖", async () => {
  const root = await mkdtemp(join(tmpdir(), "fd-cfg-"));
  await ensureMarkdownlintConfig(root);
  expect(JSON.parse(await readFile(join(root, ".markdownlint.json"), "utf8")).MD033).toBe(false);
  // 用户自定义后不被覆盖
  await writeFile(join(root, ".markdownlint.json"), '{"custom":true}\n');
  await ensureMarkdownlintConfig(root);
  expect(JSON.parse(await readFile(join(root, ".markdownlint.json"), "utf8")).custom).toBe(true);
});
