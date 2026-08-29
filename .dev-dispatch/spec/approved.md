# katana-work-folder MCP 自暴露 Prometheus /metrics（R5 服务群可观测性接入·服务端）

## 背景与事实（已由派单方核实，直接消费，勿重复勘察）
1. 本包 `mcp/work-folder/katana_work_folder_mcp/server.py` 定义 FastMCP 实例 `mcp`（`from fastmcp import FastMCP`，依赖区间 fastmcp>=3.2.4,<4；生产 venv 实测装的是 fastmcp 3.4.7）。`main()` 以 streamable-http 跑在 127.0.0.1:5602（env `KATANA_WORK_FOLDER_MCP_HOST/PORT`）。当前 `GET /metrics` → 404（实测 2026-08-29）。
2. 姊妹仓库 session-engine（Dandi007/session-engine PR #5，已部署在产）是本单样板：`@mcp.custom_route("/metrics", methods=["GET"])` 返回 `PlainTextResponse("session_engine_up 1\n", media_type="text/plain; version=0.0.4")`——零依赖、process-local、loopback 无鉴权、不触碰任何业务存储。katana 用的是 fastmcp 包（非 mcp.server.fastmcp），其 `FastMCP.custom_route(path, methods=...)` 是公开 API（fastmcp ≥2.7 起支持，3.x 保留）；starlette 随 fastmcp 依赖链可用。实现时以 venv 内实际安装版本核对该 API 存在且行为一致（测试即证明）。
3. 下游契约（并行单 fleet-sentinel 已按此名接入 scrape/告警/面板，勿改名）：exposition 单指标 `katana_work_folder_up 1`，Content-Type `text/plain; version=0.0.4; charset=utf-8`。
4. 本分支 base（c06c472）相对 origin/main（2686b47）仅多一个「只删不改」commit：删除 tracked `.dev-dispatch/`/`.dd-evidence/` 残留。该删除属基线卫生，不是本单产品范围。

## 交付物
D1 `mcp/work-folder/katana_work_folder_mcp/server.py`：新增 `GET /metrics` 自定义路由：
   - 响应体恰为 `katana_work_folder_up 1\n`；Content-Type `text/plain; version=0.0.4; charset=utf-8`。
   - process-local：不得读 kernel/store/git 状态，不得因数据面异常而 5xx——该指标的语义是「HTTP 进程活着」（进程面存活交给 Prometheus `up` 判据，二者同源即可）。
   - 不改任何既有 tool 的行为、签名与返回；不新增依赖（starlette 由 fastmcp 依赖链提供）。
D2 新测试 `mcp/work-folder/tests/`（命名对齐既有测试文件风格）：不经网络起真实端口，直接对 ASGI app（如 `mcp.http_app()` 或 fastmcp 等价物）以测试客户端请求 `GET /metrics`，断言：HTTP 200；Content-Type 以 `text/plain` 开头且含 `version=0.0.4`；响应体含 `katana_work_folder_up 1`。测试不得依赖 /data/work-records 或任何真实数据根。

## 硬约束
- 只改 `mcp/work-folder/` 上述两处；不得触碰 mcp/memory、mcp/wiki*、mcp/kernel、mcp/shared、mcp/remote 等其它包；不得改 systemd unit、不得部署、不得 restart 生产服务（部署由派单方在本单合入后执行）。
- 既有测试零回归：work-folder 包测试全绿。

## 验收命令（冻结）
```dd-acceptance
bash -lc 'V="$(mktemp -d)"; python3 -m venv "$V/venv" && "$V/venv/bin/pip" install -q -e mcp/shared -e mcp/kernel -e mcp/work-folder pytest && "$V/venv/bin/python" -m pytest -q mcp/work-folder/tests --import-mode=importlib -p no:cacheprovider'
```