# 巡检清单模板（实例化到值守卷 `checklist.md`，端口 / 路径按部署改）

> 默认部署：状态面 `:7494 /v1/lines`；看板 agent-bus `:7490`（Bearer token 文件由部署给出）；run root `/data/fleet-graph/runs/<line>/`；dd root `/data/fleet-graph/dd/<dev>/`；调度器停牌文件 `/data/fleet-graph/runs/.scheduler/<line>.json`；Prometheus `:9090`。本机回环请求前 `env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy`。

## 0. 三条铁律

1. **不代批单子**：dd 单的 gate 由线自判（D5）。单卡在 `awaiting_gate`，去查线为什么没判、修那个原因，不投裁决。
2. **只修不开发**：允许改配置、改 goal / context、改 persona 与 role registry、重启 unit、补路径、修脚本级缺陷、答 B 类升报；不允许替线写产品代码、写 spec、派单。产品缺陷立案给线。
3. **先取证再动手**：每个结论带读数与出处；对生产的写动作先想「它是不是线自己的活」。

## 1. 六个读数

| 看什么 | 怎么看 | 正常 | 不正常时 |
|---|---|---|---|
| 线活着 | `/v1/lines` 取 terminal / parked / heartbeat_age_s / generation / round / wake_facts | working 且 heartbeat < 30 min；或 blocked/parked 且 `waiting_on ∈ {dd, external}` 且所等的单确在跑 | blocked + `waiting_on=decision` → 读 question 按 goal 判 A/B；heartbeat 超时且 unit 在跑 → 判活性（`systemctl --user status` 看 CPU 与子进程、opencode.db 最后消息时间）；unit 反复几秒退出 → 读 run log |
| 在推进 | 线 progress 尾部三条（`fs_read`）；`rounds.jsonl` 最近两轮 verdict / reason | 每轮有新事实：新单、新证据、新里程碑 | 连续三轮同一 reason / 同一 evidence gap = 空转，找卡点修（多半是 goal 缺一条授权或纪律） |
| 单子 | `dd/*/record.json` 里 `dispatched_by == line` 的单：`status.json` 的 state / stage / awaiting / failure；`events.jsonl` 尾部 | running / complete / refused 各有理由；implement 在 `stage-timeout` 栅栏内 | `awaiting_gate` 超 20 min 而线没醒 → 看停牌文件与调度器 refusal，归零 streak 或清 `parked_*`；fault 且 retryable → 分传输层 / 执行层，看 `route_attempts` 与 `contract_error` 原文；同一单第三次合约违约 → 查 implement prompt 的输出合约 |
| 花钱 | 按单 / 按线分组的 token；`cost_obs:*` 比率 | 与产出成比例：一张单 complete 对应有限轮；1h implement ≈ 1700 万 input / 9 万 output 是基线 | 高消耗零产出 → 停下找原因（turn timeout、空转、重复派单、被取代的废单仍在跑）；指标恒 0 → 仪表坏了，用 `opencode-tokens.py` 直读 |
| 看板 | `board:work-notes?after_seq=<上次 head_seq>` 过滤本线 alias / line id 的 question 与 decision | evidence / progress note；gate 的 question 由线自答 | question 且是真 B 类 → 按授权边界答；`work.decision.v1` 出现 → 核 `decided_by == dispatched_by`，不是就查谁代批了 |
| 生产面 | 常驻 unit 全 active；`readlink -f <app>/current` 与 `.release-sha`；`fleet-graphd` 的 ActiveEnterTimestamp | 与开线时记录一致 | 执行位被翻 / 调度器被重启 → 找到是谁、从哪个 commit 部署、比对缺了哪些文件（`diff -rq` 两个 release 的 src）；评估对本线在跑的单与 gate 路径的影响；准备超集快照但不急于回滚 |

## 2. 花钱怎么看

- 引擎侧：dd 单目录 `agent-runs/<run>/<stamp>/launcher.stdout` 结构化结果里的 `route_attempts`（模型、耗时、http_status）；`launches.jsonl` 里实际生效的模型。
- 会话侧（最可信）：`scripts/opencode-tokens.py [hours]` 直接汇总各 run 的 `opencode.db`（input / output / cache_read，按单 / 线分组）。
- 指标侧：Prometheus `agent_runtime_tokens_*_total` 与 `cost_obs:*`；**用前先核 `usage_source` 是否 missing**——agent-run 做 conformance retry 后 `run_dir` 指向新 stamp 目录，采集器找不到会话库时指标恒 0。
- 判据：连续两小时消耗上升而 progress 无新事实，视为不赚钱，进处置阶梯。

## 3. 常见症状与修法

| 症状 | 修法（层级） |
|---|---|
| `wf_resume BROKEN` | ① context.md 路径列指向不存在的文件或分支不符；改 context.md |
| 线 blocked 在 B 类 | ① 真 B 类 → 答裁决（`work.decision.v1` 带 refs 到 question note）；假 B 类 → goal 补一条 A 类授权，goal 变更即唤醒 |
| 等单时连续 continue 触发熔断 | ① goal 补「机械驻停条」：唯一未决事实是一张在跑的单时，最多再做一轮 A 类产物就 `blocked + waiting_on=dd + dd_development_id`；驻停只能由 coordinator 信封出，worker report 不报 blocked |
| gate 滞后（单到 awaiting_gate 线没醒） | ① 停牌文件 `streak` 归零 / 清 `parked_*`；② 写进 monitor 的 autowake 自动兜底；③ 探针缺陷立案 |
| 被取代的废单仍在烧钱 | 生产不可逆：`systemctl --user stop <unit>`，立案记录，勿 adopt 勿重启 |
| 座位跑错模型 | ① 三层同扫：agents.yaml / roles/*.yaml / dd stage_models；改 role registry 后重启相关 unit |
| 失败码与现场不符 | ③ 立案（取证：事件原文 vs run 结构化结果 vs 网关探测） |
| 生产 unit 挂 | 重启，读 journal，③ 立案 |
| 执行位被别的 session 翻到旧引擎 | 取证谁 / 从哪个 commit；评估在跑的单；准备「旧生产 + 对方改动」的超集快照（`release.sh --no-flip`）；gate 卡住才翻转并通报 |
