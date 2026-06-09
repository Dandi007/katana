// 回归测试：分发产物 `node dist/cli.js` 必须在 node runtime 下跑通真实 defaultRunner。
// 历史 bug：defaultRunner 用了 Bun.spawn（bun 专有），node 下报 "Bun is not defined"。
// 单测注入 fake runner 不会碰到 defaultRunner，故必须 build 成 node 产物、用 node 实跑。
import { expect, test, beforeAll } from "bun:test";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, writeFile, chmod, readFile, mkdir } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const envelope = readFileSync(new URL("../../fixtures/sample.fetch.json", import.meta.url), "utf8");

let cliPath: string;
let fakeBinDir: string;

beforeAll(async () => {
  const work = await mkdtemp(join(tmpdir(), "fd-node-"));
  cliPath = join(work, "cli.js");
  // build 分发产物（node target），与 package.json 的 build 脚本一致
  const cliSrc = fileURLToPath(new URL("../cli.ts", import.meta.url));
  execFileSync("bun", ["build", cliSrc, "--target", "node", "--outfile", cliPath]);
  // 假 lark-cli：无视参数，回吐 fixture envelope
  fakeBinDir = join(work, "bin");
  await mkdir(fakeBinDir, { recursive: true });
  const fake = join(fakeBinDir, "lark-cli");
  await writeFile(fake, `#!/usr/bin/env bash\ncat <<'LARKJSON'\n${envelope}\nLARKJSON\n`);
  await chmod(fake, 0o755);
});

test("built cli.js 在 node 下经真实 child_process runner pull 成功（无 Bun 全局依赖）", async () => {
  const out = await mkdtemp(join(tmpdir(), "fd-out-"));
  const res = spawnSync("node", [cliPath, "pull", "--doc", "x", "--root", out, "--path", "d"], {
    env: { ...process.env, PATH: `${fakeBinDir}:${process.env.PATH}` },
    encoding: "utf8",
  });
  // 修复前这里会是 status=1 + stderr "Bun is not defined"
  expect(res.stderr).not.toContain("Bun is not defined");
  expect(res.status).toBe(0);
  const idx = JSON.parse(await readFile(join(out, ".index.json"), "utf8"));
  expect(idx.entries["PYxgdqFCioL2zYxD5YvcGUllnyd"]).toBeTruthy();
});
