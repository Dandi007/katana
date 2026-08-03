# code-reading Errors

记录本 skill 使用 / 调试 / review / 迁移中发现的 bug、回归与踩坑。

## 2026-07-29 23:43 — FastMCP Streamable HTTP 的 initialize 响应不是裸 JSON

- 场景：为 Work Folder flat MCP 的只读 canary 直接用 `urllib` 向 `/mcp` 发送 `initialize`，初版对响应体执行 `json.loads`。
- 结果：服务按 Streamable HTTP 返回 `Content-Type: text/event-stream`，JSON-RPC payload 位于 `data:` 行，初版报 `JSONDecodeError`；只完成初始化请求，未调用工具、未写入数据。
- 处置：canary 应声明 `Accept: application/json, text/event-stream`，解析 SSE 的最后一个 `data:` JSON，保存 `mcp-session-id`，发送 `notifications/initialized` 后再调用 `tools/list`/只读工具。通知本身无响应体是正常行为。

## 2026-07-29 20:10 — flat plan 的文本 content_kind 被误断言为 `text`

- 场景：用 `/retrieval:code` 对 disposable source-repair simulation 生成的 final flat plan 做机械计数 gate。
- 结果：plan 正常生成且其余计数正确，但验证脚本把文本文件的实际枚举值 `utf8_text` 误写为 `text`，导致末尾 `jq -e` 返回 1；plan artifact 本身未失败，生产数据未触碰。
- 处置：按实际 schema 使用 `content_kind=utf8_text`，并优先断言公开 API 分类 `fs_read=19378`、`fs_read_bytes=9`；验证前先枚举实际 enum。

## 2026-07-29 20:04 — `recording_sessions` 被误判为含 `status` 列

- 场景：用 `/retrieval:code` 只读核验 `douyin-live` 历史数据回填状态，同批命令先输出 SQLite schema，再查询 session 明细。
- 结果：聚合统计已成功返回，但明细查询沿用通用录制任务表习惯选择了不存在的 `status` 列，SQLite 报 `no such column: status`；readonly 连接未修改数据库。
- 处置：`recording_sessions` 的完成状态由 `ended_at`、`segment_count` 等现有列判断；schema discovery 与依赖它的查询应拆成顺序探针，后续只使用已确认列名。

## 2026-07-29 20:02 — 手工转录 source-repair SHA 漏一位导致隔离模拟提前停止

- 场景：用 `/retrieval:code` 在 disposable clone 模拟 28 条 Work Folder source mapping；第 5 条 `opencode-guide-skill-record-turn002-implementer.md` 的 SHA 从旧候选手工转录。
- 结果：把正确片段 `…2ae8ec9ad16…` 误写为 `…2ae8ec49ad16…`，hash gate 在完成前 4 个 `git mv` 后 fail-fast；只影响 disposable clone 的 staged 状态，生产 repo/远端/服务未触碰。
- 处置：从生产 baseline 重新实算并确认正确 SHA256 为 `39c37ba5bfbbd5e645214d58dd4db08945ac96b1073992ae8ec9ad16b4c5565c`；后续 mapping 必须从 canonical manifest 读取并 byte-compare，禁止再手工转录 hash。

## 2026-07-29 20:00 — PreToolUse 误解析 quoted Bash heredoc 的参数裁剪

- 场景：用 `/retrieval:code` 在隔离 clone 模拟 28 个 source repair，命令通过 `bash -s <<'BASH'` 传入受单引号保护的脚本，并使用 Bash 的 `${entry%%|*}` / `${entry#*|}` 解析固定 mapping。
- 结果：PreToolUse 在 shell 启动前报 `Bad substitution: entry%%`，整条模拟命令未执行；隔离 clone 仍 clean，生产 repo 与服务未触碰。
- 处置：quoted heredoc 也不能假设 hook 会保留 Bash 参数裁剪语义；改用 `while IFS='|' read -r ...` 的固定数据流或受审查脚本，避免 `${...#...}` / `${...%...}`。

## 2026-07-29 19:45 — Work Folder production venv 不含 pytest

- 场景：用 `/retrieval:code` 复跑 exact merge commit `53ce9758…` 的 `test_migrate_flat.py`，误把 non-editable production venv `venv-wf-flat-53ce9758` 当作测试环境。
- 结果：Python 在 collection 前返回 `No module named pytest`；没有运行测试，也没有访问或修改 Work Folder 数据。
- 处置：production venv 只用于 runtime/provenance。回归测试应使用仓库已有 dev/test venv，并保持 exact `PYTHONPATH`、`PYTHONDONTWRITEBYTECODE=1` 和禁用 cacheprovider；不能把缺少 pytest 记作产品测试失败。

## 2026-07-29 19:42 — session-harvest 现役目录被误判为 Git repo

- 场景：用 `/retrieval:code` 为 Work Folder flat cutover 的 session-harvest P2b 部署准备做只读身份核验，直接对 `/data/code/self/session-harvest` 执行 `git -C ... rev-parse`。
- 结果：该路径是无 `.git` 的现役代码目录，Git 返回 `not a git repository`，同一 fail-fast 探针后续只读 unit 检查未执行；没有修改代码、服务或数据。
- 处置：先枚举 repo/worktree identity；当前可审计基线位于 `/data/code/worktrees/Zettelkasten/session-harvest-p2a`，P2b 在独立 `/data/code/worktrees/Zettelkasten/session-harvest-p2b`。现役无 Git 目录只按部署 artifact 取证，不能把它当 source repo。

## 2026-07-29 18:31 — zsh 中把单双引号混入一个 `rg` PCRE 导致解析失败

- 场景：用 `/retrieval:code` 追查 Claude `~/.claude/transcripts` 的 producer，首次把包含 `[/\"']` 的 PCRE 内联到双引号 shell 参数。
- 结果：zsh 在执行 `rg` 前报 `unmatched "`；检索未运行，没有读取或修改产品数据。
- 处置：对路径和 schema 标记拆成多个 `rg -F` 固定字符串查询，避免在 shell 参数中拼接单双引号与 PCRE。

## 2026-07-29 18:35 — zsh 空 glob 在 OpenCode plugin 枚举前触发 `nomatch`

- 场景：用 `/retrieval:code` 检查 OpenCode plugin 是否写入 `~/.claude/transcripts`，循环直接使用 `/data/opencode/config/plugin/*`。
- 结果：目标目录无匹配文件时 zsh 报 `no matches found`，同批只读枚举提前终止；没有修改产品数据。
- 处置：对可能为空的目录改用 `find -type f -print0` 驱动读取，不依赖 shell glob。

## 2026-07-29 18:43 — 临时历史 clone 的 `rm -rf` 清理被安全策略拒绝

- 场景：用 `/retrieval:github` 将 OpenCode 历史浅克隆到 `mktemp` 目录做只读 provenance 检索，结束后尝试清理 `/tmp/opencode-history.r9DpcA`。
- 结果：命令在执行前被安全策略拒绝；产品数据未变，临时 clone 尚在。
- 处置：临时目录清理优先使用系统 trash 工具，或保留给系统 `/tmp` 生命周期回收；不得绕过 destructive-action 门禁。

## 2026-07-29 18:44 — `gio trash` 不支持当前 `/tmp` mount

- 场景：继续尝试以可恢复方式清理只读 provenance 检索产生的 `/tmp/opencode-history.r9DpcA`。
- 结果：`gio` 返回 `Trashing on system internal mounts is not supported`；目录仍在，产品数据未变。
- 处置：保留该临时 clone 交由 `/tmp` 生命周期回收；若必须立即清理，需使用当前安全策略允许的专用临时目录清理机制。

## 2026-07-13 10:36 — OpenCode provider 摘要脚本误将 model-level `provider.api` 当作必填

- 场景：用 `/retrieval:code` 对 e300-nuc OpenCode 的 provider/protocol 分流做只读求真，`jq` 试图汇总 Kimi/Z.AI 每个 model 的 `provider.api`。
- 结果：部分 model 不带该可选字段，表达式对 `null` 直接迭代，报 `Cannot iterate over null`；这是取证脚本假设错误，不是 OpenCode 配置故障。
- 处置：汇总可选 model metadata 时使用 `.provider?.api?` 并过滤 `null`；单独核对 provider-level `options.baseURL` 作为 endpoint 主断言。

## 2026-07-13 10:36 — `cc-switch-cli` 未在 NUC 当前 `PATH`

- 场景：用 `/retrieval:code` 核对 CC Switch DB 中 `app_type=opencode` 的 provider 记录。
- 结果：直接执行 `cc-switch-cli --db-path ... provider list` 报 `command not found`；前后其他只读取证正常，未修改 DB。
- 处置：先用 `rg --files` 定位现役 binary 或 repo 声明的 CLI 入口；memory card 的 `How to Verify` 使用 exact path，不依赖裸命令名。

## 2026-07-13 10:40 — CC Switch usage 表被误判为含 `request_method` / `request_path`

- 场景：希望从 `/data/cc-switch/cc-switch.db` 最近请求直接核对 OpenCode 的 Responses / Messages / Chat Completions 路径。
- 结果：`proxy_request_logs` schema 只有 provider/model/status/token/latency 等 usage 字段，查询 `request_method` 时报 `no such column`；没有 DB 写入。
- 处置：先查 `.schema`；usage 表只用于核对 provider/model 与成功状态，endpoint path 改由 CLI/provider 配置、OpenCode adapter 源码与 CC Switch route 源码交叉验证。

## 2026-07-13 15:30 — 本机无 `ruby`，不能把 Ruby YAML parser 当成通用静态门禁

- 场景：为 Loop Engine R10c Worker fleet/workflow 做只读 YAML syntax 校验，首次调用 `ruby -e 'require "yaml"; ...'`。
- 结果：本机返回 `command not found: ruby`；JSON、Git identity 与输入 manifest 校验均未受影响，编排尚未启动。
- 处置：改用目标 repo 已锁定的 `yaml` Node package，以 `node --input-type=module` 解析两份 YAML 并得到 `YAML_OK`；后续 CI/Host 静态校验不得默认 Ruby 存在。

## 2026-07-13 08:22 — 远端轻量运维机未安装 `rg`

- 场景：用 `/retrieval:code` 经 SSH 核对 `ecs-volc:/opt/gate-auth` 的授权实现与管理端点。
- 结果：按默认检索约定调用 `rg` 返回 `bash: rg: command not found`；此前已读到的源码内容有效，后续聚合检索未执行，未修改运行态。
- 处置：先用 `command -v rg` 探测远端能力；缺失时降级到 `grep -RIn`，不要把远端环境默认等同于本机开发环境。

## 2026-07-10 14:18 — NUC 环境无 `python` 命令

- 场景：核验 `/data/code/self/katana/mcp/{memory,wiki,work-folder}` 测试基线。
- 结果：三次 `python -m pytest` 均以 exit 127 失败，`zsh: command not found: python`。
- 处置：先检查项目声明的 runner 与 `uv`/`python3` 可用性，再用实际入口执行；不可把该错误记作测试失败。

## 2026-07-10 14:29 — 多个 MCP suite 不可合并为单次 pytest invocation

- 场景：用同一 Python/PYTHONPATH 一次运行 `mcp/{memory,wiki,work-folder,shared}/tests`。
- 结果：各项目均把测试包命名为顶层 `tests`，pytest collection 发生模块名碰撞，报 23 个 `ModuleNotFoundError: tests.*`；这不是产品测试失败。
- 处置：四个 suite 必须分开 invocation 执行并分别检查 exit code；当前基线不应改成合并命令。

## 2026-07-11 07:08 — 子项目裸 `uv run pytest` 未启用 dev extra 且无法解析本地 shared package

- 场景：为 KB MCP 最终设计文档重新核验 memory/wiki/work-folder/shared 四套测试基线。
- 结果：memory/shared 因默认依赖不含 pytest 报 `Failed to spawn: pytest`；wiki/work-folder 因 registry 中不存在未声明 source 的 `katana-kb-mcp-shared` 而 dependency resolution 失败。这些都不是产品测试失败。
- 副作用：uv 在 memory/wiki/shared 子目录创建了临时 `.venv`；已按精确路径清理，未触碰 7/1 已存在的 work-folder `.venv`。
- 处置：先读各 `pyproject.toml`；独立包需显式启用 `[dev]`，wiki/work-folder 还需使用 monorepo 已配置的本地 shared source、现役聚合 venv，或显式 `PYTHONPATH`。不得把裸 `uv run pytest` 当成仓库通用入口。

## 2026-07-11 10:22 — `node --input-type=module` 的 static import 不能使用模板字符串

- 场景：用 `/retrieval:code` 对 Loop Engine `fillSeedPayload → harnessResolver` 数据流做最小只读复现，需要从运行时确定的 pinned Engine 路径加载 compiled module。
- 结果：首次脚本写成 `import { ... } from \`${process.env.ENGINE_RUNNER}/dist/loader.js\``，Node 在解析期报 `SyntaxError: Unexpected template string`；由于外围诊断脚本同时错误使用 `set +e`，还打印了无效的 `MIN_REPRO_CONFIRMED`。该次证据已明确作废，没有触碰产品候选或 store 状态。
- 处置：运行时路径必须使用 `await import(path)`；诊断断言在采集两个子命令 exit code 后恢复 `set -e`，分别要求 broken case 非零、working control 为零，禁止仅凭总命令 exit 0 宣称复现成立。

## 2026-07-11 16:11 — Loop Engine `jobs --json` 顶层形状误判

- 场景：核对本轮 M1/CI drains 是否走过 jobd，并列出最近历史 jobs。
- 结果：首次把 `loop-engine jobs --json` 当作 `{jobs:[...]}`，实际当前 CLI 返回顶层 JSON array，`jq` 报 `Cannot index array with string "jobs"`。
- 处置：按 array 直接统计/筛选后重跑；确认 jobd 记录 35 条，最近多条 Dev Dispatch fleet 有 job_id，而本轮 M1/CI 路径均无 job record。

## 2026-07-11 16:24 — zsh 未命中 run glob 导致模型取证命令提前失败

- 场景：从 M1 colocated Claude JSONL 核验 rendered fleet 的 setter 最终解析成哪个模型。
- 结果：命令直接展开不存在的 `2026-07-11T105510-*`，zsh 默认 `nomatch` 使整条命令报错；没有得到该轮证据。
- 处置：改用 `find -name '2026-07-11T1055*'` 枚举真实 drain/tick dirs，再从 tick `.sessions` 提取模型；确认初轮与后续 recovery Claude sessions 均为 `claude-opus-4-8`。

## 2026-07-11 16:34 — Node `--import` 裸相对路径与 resolver 入口误写

- 场景：按 `/retrieval:code` 核验 Loop Engine CLI help 与 Model–Provider–Runtime registry。
- 结果：`node --import scripts/register-node-esm-extension-loader.mjs ...` 把 `scripts` 当 package 名解析，报 `ERR_MODULE_NOT_FOUND`；同时误用不存在的 `dist/model-resolver.js`，实际 compiled 入口是 `dist/lib/model-resolver.js`。两次均为只读诊断命令失败，没有启动 run 或改动运行态。
- 处置：`--import` 使用绝对路径（或显式 `./scripts/...`），resolver 使用 `/data/code/self/loop-engine/dist/lib/model-resolver.js`；CLI/resolver 裸 `node` 仍不可省略 extension loader。

## 2026-07-11 16:46 — `fuser -m <子目录>` 把整个挂载点报成占用

- 场景：清理已弃用的 Dev Dispatch workspace 前，尝试用 `fuser -m <精确子目录>` 做 live-process fail-safe。
- 结果：`-m` 按 mount namespace 判断，传入 `/data/vault/.runtime/...` 后列出整个 `/data` 挂载上的大量无关进程，产生假阳性并触发安全拒删；没有文件被删除、没有进程被误杀。
- 处置：目录级清理改用 `/proc/<pid>/cwd` 与 `/proc/<pid>/fd/*` 的精确路径前缀检查；`fuser -m` 只用于确实要判断整个 mount 的场景，不能验证普通子目录占用。

## 2026-07-11 23:56 — PreToolUse hook 误解析 quoted heredoc 内 JavaScript template literal

- 场景：用 `/retrieval:code` 对 shared CI attestation ZIP parser 做只读内存 PoC，shell 命令使用 `<<'NODE'` quoted heredoc，JavaScript 内含 `${h(...)}` template literal。
- 结果：PreToolUse hook 仍把 heredoc 内 `${...}` 当成 shell substitution，报 `Bad substitution: h`；Node 脚本未执行，无任何产品或运行态写入。
- 处置：诊断命令避免 heredoc 中的 JavaScript template literal，改用字符串拼接或将脚本放入已审查文件后执行。

## 2026-07-12 00:01 — OCI image archive layer 被当作未压缩 tar 读取

- 场景：用 `/retrieval:code` 核验 sealed CI image 内 Node/bwrap/libseccomp 的 exact path，从 OCI/Docker save outer tar 流式取出 layer blob。
- 结果：首次对 gzip-compressed layer 使用 `tar tf -`，逐层报 `Archive is compressed. Use -z option`；只读命令没有修改 archive。
- 处置：改用 `tar tzf -` 重跑；确认镜像仅含 `/usr/local/bin/node`、`/usr/local/bin/bwrap` 与 `libseccomp.so.2.6.0`，不含 shared wrapper 所用的 `/usr/bin/node` / `/usr/bin/bwrap`。

## 2026-07-12 00:06 — login zsh PATH 不含 `ldconfig`

- 场景：用 `/retrieval:code` 对 shared CI inner-seccomp launcher 的 host/image libseccomp ABI 做只读比对。
- 结果：首次直接调用 `ldconfig -p` 报 `command not found`；未修改系统状态。
- 处置：使用 exact `/usr/sbin/ldconfig -p` 重跑，host `libseccomp2` 为 `2.6.0-2ubuntu5`，sealed image layer 含 `libseccomp.so.2.6.0`。

## 2026-07-12 10:40 — PCRE 与 shell 单引号拼接导致 zsh 解析失败

- 场景：用 `/retrieval:code` 统计 Loop Engine `src/` 中缺 `.js` 后缀的相对 ESM import，命令试图把同时包含单双引号的 PCRE 直接内联进 zsh。
- 结果：zsh 在执行前报 `parse error near ')'`；统计未运行，未修改任何产品源码或运行态。
- 处置：避免在 shell 中拼接含两类引号的复杂 PCRE；改用 `rg` 的简化多阶段筛选，或把固定 pattern 放入受审查的脚本/文件后执行。

## 2026-07-12 10:49 — zsh 的 `status` 是只读特殊参数

- 场景：用 `/retrieval:code` 比较 brainstorm 与 memory-audit Plugin 的同名 JavaScript helper 是否逐字相同，shell 循环把比较结果赋给变量 `status`。
- 结果：zsh 报 `read-only variable: status`，hash 对比循环在第一项前中止；此前的文件行数与导出函数盘点已正常完成，未修改任何产品文件。
- 处置：zsh 脚本避免使用 `status` 等特殊参数名，改用 `cmp_result`、`state_label` 等普通变量后重跑。

## 2026-07-12 23:09 — PreToolUse hook 误解析 zsh `${(z)parameter}` 展开

- 场景：为 Loop Engine release 恢复 GitHub credential 时，只读取 secret-service 命中状态；shell 循环原计划用 zsh `${(z)attrs}` 把固定 attribute 字符串拆成参数。
- 结果：PreToolUse hook 在命令执行前报 `Bad substitution`，探针未运行、没有读取或输出任何 secret。
- 处置：安全探针不用 zsh 参数展开动态拼 argv；改成逐条固定参数的 `secret-tool lookup` 调用，并只输出命中布尔值/长度，不输出 secret 内容。

## 2026-07-28 23:07 — 本机 FastMCP Client 连接 localhost 误吃 SOCKS proxy

- 场景：用 `/retrieval:code` 读取 subagent-mcp 的 acceptance 调用方式后，通过 `fastmcp.Client("http://127.0.0.1:5607/mcp")` 做部署后端到端验证。
- 结果：client 在发出 MCP 请求前初始化 httpx proxy transport，因环境中的 SOCKS proxy 与 venv 未安装 `socksio` 报 `ImportError`；没有提交 subagent，也没有产生 proof 文件。
- 根因：FastMCP/httpx 默认 `trust_env=True`，即使目标是 localhost，也会继承代理环境；不能假设 loopback 地址一定自动 bypass。
- 处置：仅对验证进程 `unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy` 后重跑，MCP run 成功并写出 proof。后续本机 FastMCP client 探针应显式禁用代理环境，或复用目标仓库 `tests/conftest.py` 的 `trust_env=False` 包装。

## 2026-07-28 23:12 — Development MCP release 被误假设为 `src/` 布局

- 场景：只读定位 `dev_le_adddir_01` 的 `MR_AMBIGUOUS`，准备检索现役 release `30dc64d...` 的 acceptance/controller 实现。
- 结果：首次把该 Python release 误当成常见 `src/` layout，`rg` 返回 `.../src: No such file or directory`；没有修改产品代码、DB、服务或 GitHub。
- 处置：先用 `rg --files` / `find -maxdepth` 枚举 release 布局，再从顶层 `loop_engine_development_mcp/` 读取 `reconciler.py`、`acceptance.py` 等现役源码。

## 2026-07-28 23:16 — Dev Dispatch live evidence 探针未先固定 JSON / 路径 / DB schema

- 场景：用 `/retrieval:code` 只读补齐 `dev_le_adddir_01` 的冻结 Acceptance 证据。
- 结果：一次 `jq 'keys, . | ...'` 把 `keys` 产生的 array 继续按 object 索引；一次已进入 workspace 后仍给 `sha256sum` 加 `workspace/` 前缀；另一次假定现役 SQLite 有 `materialization_intents` 表。三次均只使诊断命令失败，没有修改 Development、Git 或运行态。
- 处置：JSON 先单独输出 `keys` 再投影；相对路径以实际 `workdir` 为基准；SQLite 必须先查 `.tables` / `.schema`。本次有效证据已改由 MCP normalized evidence、冻结 `.dd-evidence/acceptance.json` 与精确 workspace Git 对象交叉核验。

## 2026-07-29 01:31 — zsh 循环变量 `path` 覆盖特殊 `$path` 数组

- 场景：用 `/retrieval:code` 轮询 Loop Engine canary JobD 的候选 HTTP 路径，循环写成 `for path in ...`。
- 结果：zsh 将 `path` 视为与 `PATH` 绑定的特殊数组；赋值后当前 shell 的命令搜索路径被候选 HTTP 路径覆盖，后续三次 `curl` 均报 `command not found`。HTTP 请求没有发出，服务和文件均未变更。
- 处置：zsh 脚本不得把 `path`、`status` 等特殊参数用作普通循环变量；改用 `endpoint_path` 等任务专用名称后重跑。

## 2026-07-29 01:32 — JobD HTTP `/jobs` 响应被误当成顶层数组

- 场景：修复 zsh `path` 变量问题后，继续用 `/retrieval:code` 查询 canary JobD active jobs。
- 结果：首次对 `/jobs` 响应直接执行 `.[] | select(.state...)`；该 HTTP API 实际返回 `{"jobs":[...]}`，`jq` 报 `Cannot index array with string "state"`。请求只读成功，服务和文件未变更。
- 处置：CLI `loop-engine jobs --json` 与 HTTP `/jobs` 的顶层形状不同；先输出 `type`/`keys`，HTTP 路径按 `.jobs[]` 解析。重跑确认 canary active jobs 为 0。

## 2026-07-29 01:49 — JobD `/proc/<pid>/exe` 取证漏用 sudo

- 场景：runtime-only 切换 Loop Engine canary 后，用 `/retrieval:code` 交叉验证 systemd MainPID 实际 executable、SHA256 与 inode。
- 结果：普通用户对该跨 group 进程的 `/proc/<pid>/exe` 无跟随权限；首次 `readlink`/`sha256sum`、第二次 `stat -L` 分别返回 `Permission denied`，使两段 `set -e` 验证脚本在后续只读检查前退出。canary service、runtime drop-in 和 binary 切换均已成功，不受影响。
- 处置：该服务的 `/proc` executable 取证从一开始统一使用 `sudo -n readlink -f`、`sudo -n sha256sum`、`sudo -n stat -L`。重跑确认 `/proc` 与安装 artifact inode 均为 `89434532`、size `125897920`、SHA256 `42651c0fd4f74ba07c7858663c6d650d498a9b34a9820fc271af5b739b2af957`。

## 2026-07-29 09:34 — bootstrap manifest 字段名被误判为 `.commit/.tree`

- 场景：用 `/retrieval:code` 核对 PR #266 bootstrap artifact 的 exact source identity，首次按通用构建清单习惯查询 `.commit` 与 `.tree`。
- 结果：两项均返回 `null`；实际 manifest 使用 `.engine_commit` 与 `.engine_tree`。该次只读查询没有修改 artifact、服务或 Development。
- 处置：先输出 manifest 顶层 `keys`，再按实际 schema 查询；本次有效值为 `engine_commit=2a87791de764ce14dce9669069fc42ff1ef3085e`、`engine_tree=32689af2cf3a7a6cb3bd59e8966151d87654007e`。

## 2026-07-29 09:34 — 跨 group JobD 的 `/proc/<pid>/environ` 取证漏用 sudo

- 场景：用 `/retrieval:code` 核对 production JobD actor 继承的 runtime 环境与 Claude provider 配置，首次直接读取 `/proc/<pid>/environ`。
- 结果：普通用户收到 `Permission denied`；没有输出环境变量，也没有修改进程、服务或配置。
- 处置：跨 group 的 `/proc/<pid>/{exe,environ}` 统一从首次探针起使用 `sudo -n`；读取 environ 时只筛选任务需要的非敏感键名/值，禁止把完整环境或 secret 输出到日志。

## 2026-07-29 09:40 — zsh 将 `$commit:path` 解析为参数修饰语

- 场景：用 `/retrieval:code` 从 exact Plugin commit 读取 capability manifest，命令写成 `git show "$commit:contracts/attempt-context-capability.json"`。
- 结果：zsh 未保留预期的 `<commit>:<path>` 分隔，Git 收到拼坏的 revision 并报 `ambiguous argument`；只读命令在 digest oracle 前 fail-fast，未修改 release 或运行态。
- 处置：变量后紧邻冒号时显式加边界，写成 `git show "${commit}:contracts/attempt-context-capability.json"`；任何 `<rev>:<path>` 动态拼接都采用 `${rev}:...`。

## 2026-07-29 09:47 — Plugin Acceptance artifact 被误投影为 release manifest

- 场景：用 `/retrieval:code` 复核 exact Plugin release 的 committed `.dd-evidence/acceptance.json`，首次按 release manifest 习惯查询 `status/commit/plugin_commit/capability_*` 等顶层字段。
- 结果：除实际存在的 `passed=true` 外其余投影均为 `null`；该文件实际是 `acceptance_result`，顶层为 `command_results/subject_commit/input_commit/...`，capability identity 在独立 committed manifest 与 digest oracle 中。只读查询未修改 release。
- 处置：先 `jq 'keys'` 确认 artifact kind/schema；Acceptance 结果按 `passed` 与各 `command_results[].exit_code` 核对，Plugin capability identity 单独运行 `scripts/plugin-digests.sh`，不要混用两种 artifact schema。

## 2026-07-29 09:57 — `attempt-context.py bootstrap --help` 不是受支持的子命令帮助

- 场景：用 `/retrieval:code` 核对 Plugin release `849197d...` 的 H0 bootstrap CLI，按常见 argparse 习惯调用 `python3 scripts/attempt-context.py bootstrap --help`。
- 结果：该脚本的自有 `parse_args` 将 `--help` 视为未知 bootstrap 参数并以 `INVALID_INPUT` 退出，后续同一 fail-fast 取证命令未继续；没有写入 H0 或修改 release。
- 处置：顶层 `--help` 只列 command；子命令参数必须直接读 `cmd_bootstrap` 的 `parse_args` 声明。当前 bootstrap 必填为 `--development-id`、`--target-base-commit`、`--spec`、`--out-root`。

## 2026-07-29 10:07 — JobD `job.json` 被按未经确认的通用字段投影

- 场景：用 `/retrieval:code` 只读定位 Development `dev_cancel_resume_02` 的三份 PR #83 Final Reviewer raw output，首次按通用 JobD 记录习惯直接投影 `id/run_id/state/...`。
- 结果：投影字段均为 `null`；实际 `job.json` schema 与假定不同。该次查询只读，未修改 job、run、Development 或产品文件。
- 处置：对 JobD artifact 先输出顶层 `keys` 并读取实际 schema，再按已确认字段关联 run；本次 raw fixture 最终以 exact run 目录、原始 bytes SHA256 和 outer result 内的结构化 payload 为准。

## 2026-07-29 10:31 — 对 `/data/loop-engine` 的未充分限域检索失控

- 场景：用 `/retrieval:code` 定位旧 Gateway Development `dev_01KYJ48YXB7ZQAPTA49Q5T7KCZ` 的 frozen spec/evidence，先后对 `/data/loop-engine` 做 broad `rg` 与 `find`。
- 结果：两次 `rg` 在 30 秒内无返回后被手工中止；一次 `find` 递归进入大量 OpenCode runtime DB，产生约 75K tokens 并被截断。全部是只读取证，没有修改 runtime、DB、Git 或产品文件。
- 处置：旧 Development 检索应先查已知 production/canary SQLite 的 `.tables/.schema`，再按 development ID 精确 SQL；会话证据走 `/retrieval:agent-session-search`，代码与 H0 走 exact Git object/GitHub ref。禁止从 `/data/loop-engine` 根做无边界全文或文件枚举。

## 2026-07-29 12:26 — Dev Dispatch canonical spec 路径误写成旧单文件形状

- 场景：用 `/retrieval:code` 为 ProcD 与 Gateway 准备部署 runbook，首次直接读取 worktree 下 `.dev-dispatch/spec.md`。
- 结果：ProcD 路径不存在，`set -e` 使同批后续只读探针提前中止；没有修改产品、Development、服务或运行态。
- 处置：先枚举 exact `.dev-dispatch` 树；当前 canonical spec 位于 `.dev-dispatch/spec/approved.md`，manifest 位于 `.dev-dispatch/spec/manifest.json`，后续按该形状读取。

## 2026-07-29 12:29 — zsh 未按空格拆分 quoted repo/revision tuple

- 场景：用 `/retrieval:code` 批量投影 ProcD/Gateway 历史 commit identity，把 `"<repo> <revision>"` 作为 quoted 循环元素，再用 `set -- $repo_spec` 拆分。
- 结果：zsh 默认不执行 sh 风格 word splitting，`set -u` 在读取 `$2` 时中止；没有修改产品、Git、服务或运行态。
- 处置：zsh 中改用两个显式等长数组或逐条函数调用传参，禁止依赖隐式 `SH_WORD_SPLIT`。

## 2026-07-29 13:08 — Controller reconcile failure 表排序字段误用 `created_at`

- 场景：用 `/retrieval:code` 只读盘点 Loop Engine FAILED/CANCELLED Developments，在已输出 `development_reconcile_failures` schema 后批量查询目标记录。
- 结果：查询 `ORDER BY development_id,created_at`，但该表只有 `first_failed_at` / `last_failed_at` / `updated_at`，SQLite 报 `no such column: created_at`；没有修改 DB、Development、Git 或运行态。
- 处置：按已确认 schema 使用 `ORDER BY development_id,first_failed_at`；后续对刚读取的 schema 直接复用实际列名，不套用事件表惯例。

## 2026-07-29 13:11 — Canary 全局表被误按 `development_id` 列过滤

- 场景：用 `/retrieval:code` 只读核对 Kimi recovery Development 的 create command 与后续观测；同批已先输出 `audit_events`、`runtime_observations` schema。
- 结果：仍对两表直接使用 `WHERE development_id=...`；前者 ID 只可能出现在 `payload_json`，后者是无 Development 维度的全局单例，SQLite 均报 `no such column: development_id`。没有修改 DB、Development、Git 或运行态。

## 2026-07-29 15:20 — Plugin digest oracle 不能把 release 目录当隐式参数

- 场景：为 Development MCP schema-neutral release 做 disposable deployment rehearsal，复核 pinned Plugin `849197d...` 的 capability/bundle identity。
- 结果：首次在 Plugin release 目录直接执行 `scripts/plugin-digests.sh`，脚本以 `missing required argument for PD_ROOT` 退出；没有修改 Plugin、MCP、DB、service 或 Git。
- 处置：先读脚本 Usage，并显式传入 `--plugin-root <exact-release-dir> --commit <40-hex>`。重跑确认 capability `sha256:d66f...`、bundle `sha256:682a...`、workflow `sha256:840e...`。
- 处置：`audit_events` 用 `json_extract(payload_json, '$.development_id')`（并按 type/时间限域），`runtime_observations` 读取单例全局状态；禁止把 `commands` 的列模型套到全局表。

## 2026-07-29 17:55 — proxy-infra 验证命令误用工作目录和生成入口

- 场景：用 `/retrieval:code` 实现并验证 e300-nuc Mihomo reboot hardening。
- 结果：一次从 repo 根运行 `pytest`，现有测试按 `client/` 相对路径读取 fixture，产生 19 个 `FileNotFoundError`；一次在 `client/` 误调用不存在的 `./generate.sh`；更早一次将 shell 变量误写成 `.venv_runner=...`。这些命令均在修改/部署前 fail-fast，没有造成产品或运行态变更。
- 处置：该 repo 的 client 测试固定从 `client/` 启动；生成器入口先由 `rg --files`/`--help` 确认，使用 `python generate.py --client nuc-mihomo-tun`；shell 变量名不得以 `.` 开头。纠正后全量测试 28/28、Mihomo config test 和在线部署验证均通过。

## 2026-07-29 18:33 — shell 内联 PCRE 混用单双引号导致 OpenCode 路径检索未执行

- 场景：用 `/retrieval:code` 核对 `/data/claude/.claude/transcripts` 是否由 OpenCode 导出，原命令在双引号包裹的 `cmd` 中内联同时含单双引号的正则。
- 结果：zsh 在执行前报 `unmatched "`；检索没有运行，也没有读取或修改任何 session 数据。
- 处置：不要在 shell 命令里拼接同时含单双引号的复杂 PCRE。改成多个固定字符串的 `rg -F` 分步检索，或先定位候选源码文件再使用简单 pattern。

## 2026-07-29 18:35 — readonly SQLite 连接误调用 WAL checkpoint

- 场景：用 `/retrieval:code` 核对 OpenCode 原生 session SQLite 的 WAL 边界，已通过 `sqlite3 -readonly` 打开 `/data/opencode/share/opencode.db`。
- 结果：同一探针在成功读取 `PRAGMA journal_mode` 后误调用会尝试写锁/写入的 `PRAGMA wal_checkpoint(PASSIVE)`，返回 `disk I/O error (10)`；没有修改数据库、WAL、服务或 session 数据。
- 处置：readonly 取证只读 `journal_mode` 与 DB/WAL/SHM 文件元数据；如需 checkpoint 状态，改用不触发 checkpoint 的 SQLite API/状态检查。不得在 readonly 连接中调用任何 `wal_checkpoint` 变体。

## 2026-07-29 18:58 — session-engine 取证误用未安装的裸 `python`

- 场景：用 `/retrieval:code` 审查 session-engine 的并发执行模型；同批 `rg` 已成功，末尾为定位 FastMCP 源文件调用了裸 `python`。
- 结果：shell 返回 `command not found: python`；没有修改 session-engine、服务或 session 数据。
- 处置：该 repo 的 Python 命令固定使用 `.venv/bin/python`，不假设系统提供裸 `python`。

## 2026-07-29 19:22 — Development MCP `commands` 表被误假定保存 `payload_json`

- 场景：用 `/retrieval:code` 只读复核 production `attempt-context/v1` 的 H0 bootstrap 惯例；同批命令已先输出现役 SQLite `commands` 表 schema。
- 结果：随后仍查询不存在的 `payload_json` 列，SQLite 报 `no such column: payload_json`；没有修改 Development DB、Git、PR 或运行态。
- 处置：现役 `commands` 只保存 `payload_digest`、`result_json`、`error_json` 等字段，不保存 create request 原文。H0/`initial_handoff` 契约必须从 pinned MCP 源码、Plugin producer、Git/MR 一手对象和 normalized evidence 求真，不能从该表反向提取请求 payload。

## 2026-07-29 20:24 — Docker inspect Go template 直接访问可缺省字段导致探针报错

- 场景：用 `/retrieval:code` 只读核对 `douyin-live` 三个容器的 image、health、restart policy、stop signal 与 Compose provenance。
- 结果：模板直接访问 `.Config.StopSignal` 和 `.State.Health`；前者在 recorder/backend inspect map 中缺省，后者在 nginx 中缺省，Docker 报 `map has no entry for key`。此前的 Compose service/ps、进程树与 systemd 枚举均成功，没有修改容器或服务。
- 处置：对 Docker inspect 的可选 map 字段先输出 JSON 后用 `jq` 的 `//` / `?` 处理，或使用保证存在的 `.HostConfig.RestartPolicy.Name` 等字段；不得假设所有容器都声明 healthcheck/stop signal。

## 2026-07-29 20:24 — douyin-live 测试取证命令混用单双引号导致 zsh 解析失败

- 场景：用 `/retrieval:code` 只读定位 `douyin-live` 的测试框架及 `live_title` 调用链，首次将包含 Python 单双引号的多个 `rg` pattern 拼进一条 zsh 命令。
- 结果：zsh 在执行检索前报 `unmatched '`；没有读取测试结果，也没有修改目标仓库、服务或数据。
- 处置：拆成多个固定字符串或简单 pattern 的 `rg` 调用；不要在同一个 shell 参数中拼装同时含单双引号的复杂表达式。

## 2026-07-29 20:25 — zsh 空 workflow glob 中断 douyin-live 取证

- 场景：用 `/retrieval:code` 枚举 `douyin-live` 的 GitHub Actions，循环同时展开 `.github/workflows/*.yml` 与可能为空的 `.github/workflows/*.yaml`。
- 结果：仓库没有 `.yaml` 文件，zsh 的 `nomatch` 在读取任何 workflow 前中断命令；没有修改目标仓库、服务或数据。
- 处置：改用 `rg --files .github/workflows` 或 `find -type f` 枚举后逐个读取，不对可能为空的后缀使用裸 glob。

## 2026-07-29 20:41 — Docker top 自定义格式缺少 PID 字段

- 场景：部署 `douyin-live` 前用 `/retrieval:code` 取证形成的门禁命令统计 recorder 内 FFmpeg，调用 `docker top recorder -eo comm`。
- 结果：Docker daemon 返回 `Couldn't find PID field in ps output`；管道末端的 `awk` 仍输出 0。独立的数据库 active-session 门禁为 0，且随即停止容器并确认数据库无持有者，没有中断录制或损坏数据。
- 处置：Docker top 门禁使用默认输出或包含 `pid` 的格式，例如 `docker top recorder -eo pid,comm,args`；脚本同时启用 `pipefail`，禁止把上游探针失败折叠成“活动进程为 0”。

## 2026-07-29 21:55 — sealed release checkout 的跨 UID Git 读取未显式声明 safe.directory

- 场景：用 `/retrieval:code` 为 Work Folder flat production orchestration 核对 sealed release `53ce9758…` 的 Git HEAD/cleanliness；该只读 release 由 root 持有，当前取证用户为 `uther`。
- 结果：两次 `git -C <release> rev-parse/status` 均在读取对象前以 `detected dubious ownership` 失败；同批对 venv interpreter 和 production data repo 的只读取证正常，没有修改 release、production repo、systemd 或 remote。
- 处置：对跨 UID 的 sealed checkout 只读核验使用命令级 `git -c safe.directory=<exact-release> -C <release> ...`，不得写 global config；同时继续以 frozen commit/path 与 migrator bytes SHA-256 交叉绑定。

## 2026-07-29 22:12 — broad `rg` 进入单行 production simulation JSON 导致输出失控

- 场景：用 `/retrieval:code` 搜索既有 mount-namespace / rehearsal 线索，检索范围同时包含 `/data/vault/.runtime/work-folder-phase1`，其中有约 16 MB 的单行 plan/inventory JSON 与完整 simulation payload。
- 结果：`rg` 命中单行 JSON 后产生约 92 MB 原始输出并被工具截断；命令只读，未修改 production、runtime、systemd 或 remote，但该部分结果不可用。
- 处置：runtime 检索必须先用 `rg --files` 限定到 `.md/.py/.sh` 等小型文本候选，显式排除 `*.json`、simulation tree 与 manifest；禁止对含单行大 JSON 的 runtime 根做 broad content search。

## 2026-07-29 22:23 — embedded Python 静态编译探针的 shell quote 拼接失败

- 场景：对 Work Folder flat cutover Bash heredoc 中的 embedded Python 做 AST compile，首次在 `python3 -c` 的 shell 参数里混合 `<<'PY'` 的多层单双引号。
- 结果：外层 shell 拼出了非法 Python source，探针自身报 `SyntaxError`；此前 `bash -n` 已独立通过，目标 `.partial` 未被执行或修改，production/systemd/remote 未触碰。
- 处置：heredoc 提取 marker 用 Python 内的 `chr(39)`/字符串拼接构造，或使用受审查的独立静态检查脚本；不在 shell 参数中手工嵌套三层 quote。

## 2026-07-29 22:26 — 当前 login PATH 不含 ShellCheck

- 场景：对 Work Folder flat cutover `.partial` 做静态 gate，按裸 `shellcheck` 调用检查版本并运行 warning 级扫描。
- 结果：两次调用均在启动前返回 `command not found`；`bash -n` 与 embedded Python compile 已通过，目标脚本、production、systemd、remote 均未修改。
- 处置：先从既有 sealed/review toolchain 定位 pinned ShellCheck v0.10.0 的 exact binary/container，再以绝对路径运行；不得把 PATH 缺失记作脚本 lint 失败。

## 2026-07-29 22:27 — ShellCheck image ENTRYPOINT 被重复写入 argv

- 场景：定位到本机已有 digest-pinned `koalaman/shellcheck:v0.10.0` 后，对 cutover `.partial` 运行 containerized lint；首次把 `shellcheck` 再作为 `docker run IMAGE` 后的首参数传入。
- 结果：image 自带 `ENTRYPOINT ["shellcheck"]`，实际命令变成 `shellcheck shellcheck ...`，第二次调用报 `openBinaryFile: does not exist`，没有完成 lint；只启动了 network-none/read-only container，未修改目标脚本或 production。
- 处置：对该 image 只传 ShellCheck 参数 `-S warning /work/script.sh`，不重复 executable 名。

## 2026-07-30 00:12 — Work Folder mutation ledger 表名被误设为 `mutations`

- 场景：用 `/retrieval:code` 只读核验已恢复 `fs_create` 的 SQLite idempotency receipt，首次直接按通用命名查询 `mutations` 表。
- 结果：`sqlite3 -readonly` 返回 `no such table: mutations`；未产生数据库写入，Git recovery commit 与文件 hash 的独立证据均不受影响。
- 处置：先用只读 `.tables` / `.schema` 枚举实际 schema，再按已确认的表名和字段查询；不要从模块名或数据库文件名推导表名。

## 2026-07-30 00:40 — `npm test` 的生命周期前导文本被误当成 Vitest JSON

- 场景：用 `/retrieval:code` 求真 Gateway 现役 Vitest 2 JSON reporter 形状，首次将 `npm test --prefix web -- --reporter=json ...` 的 stdout 直接管给 `jq`。
- 结果：npm 在 reporter JSON 前输出 package/script 生命周期文本；`jq` 在第 2 行报 `Invalid numeric literal`，且所选临时 title pattern 未命中。只读测试探针未修改 repo、Development 或运行态。
- 处置：机械 verifier 不得从 `npm test` 混合 stdout 猜 JSON 边界；使用 exact `web/node_modules/.bin/vitest run ... --reporter=json` 子进程捕获 reporter bytes，先断言 exit code，再对唯一 JSON document 做严格解析与 exact title/status 校验。

## 2026-07-30 00:41 — Gateway 本地 main 被误当成 supporting subject 的测试树

- 场景：继续求真 Gateway focused Vitest JSON 形状时，首次直接在落后远端 10 个提交的本地 `main` checkout 查 `web/test/progressiveLoadingRegression.test.tsx`。
- 结果：该旧 checkout 尚无此文件，`rg` 报路径不存在，随后 Vitest 按 file filter 返回 `No test files found`；没有修改 repo、Development 或运行态。
- 处置：测试/fixture 求真必须从 exact supporting commit `01af2177…` 的 Git object 或隔离 checkout读取，不得把任意当前 checkout 等同于 supporting tree；执行前先用 `git cat-file`/`git ls-tree` 固定 subject identity。

## 2026-07-30 02:26 — session-harvest 的 src layout 未被裸 pytest 自动发现

- 场景：用 `/retrieval:code` 为 session-harvest P2B 的 SQLite v4→v5 lineage canary 运行定向回归，首次直接调用全局 `pytest -p no:cacheprovider`。
- 结果：pytest 在 collection 阶段因 `ModuleNotFoundError: session_harvest` 退出；测试正文未执行，数据库仅会创建在 pytest 的临时目录，repo、production queue 与服务均未修改。
- 处置：该 repo 的 package 位于 `src/session_harvest` 且未安装进当前全局环境；使用 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider ...`，并继续把 collection failure 与产品测试失败分开记录。
