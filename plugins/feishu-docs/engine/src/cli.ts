import { pull } from "./sync/pull";

const [cmd, ...rest] = process.argv.slice(2);

function flag(name: string): string | undefined {
  const i = rest.indexOf(`--${name}`);
  return i >= 0 ? rest[i + 1] : undefined;
}

if (cmd === "pull") {
  const root = flag("root") ?? process.env.KATANA_FEISHU_DOCS_ROOT ?? "docs/feishu";
  const docUrl = flag("doc");
  if (!docUrl) { console.error("missing --doc"); process.exit(2); }
  const relPath = flag("path") ?? "";
  pull({ root, docUrl, relPath })
    .then((r) => console.log(JSON.stringify(r)))
    .catch((e) => { console.error(e?.message ?? e); process.exit(1); });
} else {
  console.error("usage: feishu-docs pull --doc <url> [--path <rel>] [--root <dir>]");
  process.exit(2);
}
