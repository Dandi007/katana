# memory scanner: Rust → shell rewrite + packaging fix

**Date:** 2026-06-08
**Status:** implemented

## 要什么

memory 插件的 SessionStart 注入（`<memory-index>`）必须在 Claude Code 和 OpenCode 两个 runtime 上都可靠生效，且随 npm 包原样分发、零构建即可运行。

## 背景：被发现的真实故障

在 OpenCode 侧，一个真实 session 的启动注入里**完全没有** `<memory-index>`（也没有 work-folder 约定块），导致 agent 上下文里没有任何 memory card，无法检索记忆。

根因有两层，同源于发布包缺失运行时资产：

1. **memory（二进制分发不匹配 npm）。** 原实现是 Rust binary `claude-memory-scan`，hook 通过「缓存二进制 → GitHub Release 预编译下载 → `cargo build` → graceful skip」四级兜底解析。npm `files` 白名单只打包 `hooks/**`、`skills/**`、`plugin.json`，**不含二进制，也不含 Rust 源**；于是在干净安装环境里：Tier1 无缓存 → Tier2 下载 504 → Tier3 无 `Cargo.toml` → Tier4 `exit 0` 空输出。graceful skip 把失败**静默**了。
2. **work-folder（rules 没进包）。** 其 hook 读 `rules/work-folder.md` 注入，但 `files` 白名单不含 `plugins/*/rules/**`，包里没有该文件 → `content` 空 → `exit 0` 空输出。

为什么回归没拦住：双 runtime e2e harness 存在，但 ① 只跑工作树（二进制已编译、rules 都在），从不对 `npm pack` 产物跑；② CI 发布前只跑 `bun test`，而该测试 `mock.module('node:child_process')` 把 spawn 整个 mock 掉，真 hook / 二进制解析从不执行；③ graceful skip 设计为静默。三重叠加把 bug 全遮住。

## 决策

1. **memory 改为纯 shell + awk**，硬删除 Rust。理由：扫文件 + 解析固定 frontmatter + 拼 JSON 是 shell 量级逻辑（原 `main.rs` 255 行）；shell 脚本走现有 `hooks/**` 白名单天然进包，无构建、无下载、无版本门禁，**删除整类「二进制没拿到」失败**。
2. **修打包白名单**，`files` 增加 `plugins/*/rules/**`，连带修好 work-folder。
3. **补回归**：新增 `tests/pack-parity.sh`，对 `npm pack` 产物在干净环境跑 hook 并断言注入非空；新增字节级 golden 测试；用 `tests.yml` 在 PR 上跑。

## 实现

- `plugins/memory/hooks/scan-memory.awk`：扫描 `--system`/`--project` 两目录的 `*.md`，逐行解析 `name`/`description`/`status`/`metadata.type`，按 type 分组（BTreeMap 升序），输出 `hookSpecificOutput.additionalContext`。
- `plugins/memory/hooks/session-start`：解析路径（env > `.katana` > 默认）+ 字节序文件排序（对齐 Rust PathBuf sort）+ 调度 awk；空语料 `exit 0`。
- 删除：`src/`、`Cargo.toml`、`Cargo.lock`、`tests/e2e.rs`、`tests/session-start-fallback.sh`、`docs/dev/rust-rewrite/`、旧 rust-rewrite spec、`.github/workflows/release.yml`。

## Parity

字节级对齐**原 Rust 二进制在真实 72 卡语料上的输出**（已验证 `diff` 为空），并冻结 `tests/fixtures/expected.json` 作 golden。

唯一刻意差异：当 plain scalar 含 ASCII `: `（冒号+空格）时，`serde_yaml` 报错并丢弃整卡，而逐行解析保留该卡。真实语料无此情况（卡用全角 `：`），且保留卡优于静默丢数据。解析约定见 `CLAUDE.md`。

## How to Verify

```bash
# 字节级 golden 回归
bash plugins/memory/tests/scan-memory.test.sh
# 打包产物注入回归（packs real tarball, runs hooks from it）
bash tests/pack-parity.sh
# 既有门禁
bun test parity/adapter/opencode/ && ./tests/lint-structure.sh
```
