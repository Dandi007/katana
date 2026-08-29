# katana-memory-mcp 仓侧 /metrics 接入（katana repo）

## 背景与体检结论（真机实测 2026-08-29）
- `katana-memory-mcp.service`（systemd --user）运行 2 周 3 天（since 2026-08-12），
  `python -m katana_memory_mcp.server`，监听 `127.0.0.1:5605`。
- `GET /metrics` 与 `GET /` 均返回 404——可观测面全空白：无 /metrics 指标、
  Prometheus 无 scrape job、rules/ 无告警规则、Grafana 无面板、无演练通道。
- 服务器形态：FastMCP + uvicorn + Starlette；顶层 `build_app(data_root)` /
  `build_remote_app(...)` 返回 Starlette，多租户 FastMCP 实例挂 `/t/{tenant}/mcp`。

## 样板（勿发明，照此形态）
姊妹 work-folder MCP 的 /metrics 已由 dev-fg-6abec86fda07 / PR #153 合入
（`mcp/work-folder/katana_work_folder_mcp/server.py`）：
`@mcp.custom_route("/metrics", methods=["GET"])` 返回
`PlainTextResponse("katana_work_folder_up 1\n", media_type="text/plain; version=0.0.4")`，
测试 `mcp/work-folder/tests/test_metrics.py` 用 httpx ASGITransport 断言
status 200 / content-type text/plain / `katana_work_folder_up 1`。

## 交付范围（全部落在 katana repo）
1. `mcp/memory/katana_memory_mcp/server.py`：为生产实际服务的顶层 app
   （`build_app` 与 `build_remote_app`，二者共用同一指标语义）新增 `GET /metrics`
   路由，返回 `katana_memory_up 1\n`，`media_type="text/plain; version=0.0.4"`。
   要求：process-local、零依赖、不触碰 kernel/store/git，数据面异常不得令 /metrics 5xx
   （进程面存活交给 Prometheus `up` 判据，二者同源即可）。不改既有 `/t/{tenant}/**` 路由与行为。
2. `mcp/memory/tests/test_metrics.py`（新增）：用 httpx ASGITransport 断言
   `GET /metrics` → 200、content-type 以 text/plain 开头且含 `version=0.0.4`、
   正文含 `katana_memory_up 1`。
3. 卫生：`git status --short` 为空；本单不执行部署（生产部署由既有 flat-release +
   systemd restart 流程自理）。

## 判据对照（goal.md §判据 1-5）
1. 指标可查：`katana_memory_up` 经 `127.0.0.1:5605/metrics` 暴露；
2-5. 平台侧（fleet-sentinel scrape job + 告警规则 + Grafana 面板 + drill file_sd 演练通道）
由后续 fleet-sentinel 单承接；本单只做仓侧，验收即下方命令。

```dd-acceptance
bash -lc 'V="$(mktemp -d)"; python3 -m venv "$V/venv" && "$V/venv/bin/pip" install -q -e mcp/shared -e mcp/kernel -e mcp/memory pytest httpx && "$V/venv/bin/python" -m pytest -q mcp/memory/tests --import-mode=importlib -p no:cacheprovider'
```