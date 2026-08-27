# Work-folder governance robustness: B1-B4

Implement and review only through dev-dispatch. Preserve atomicity, CAS,
auditability, append-only records, and the Zero-mount invariant.

## B1 scope isolation

Dirty `wf-X/` must not block a governed mutation in `wf-Y/`; dirt in the target
folder or shared governance controls remains fail-closed. Preserve the existing
byte-preservation and committed-diff checks in `tests/test_scope_guard.py`.

## B2 governed recovery

`wf_reconcile` must automatically adopt tracked, diffable, conflict-free,
append-only accounting residue for its folder, preserving the data. Unsafe
residue remains refused. Provide an MCP-only manual recovery tool with dry-run
and per-file confirmation; clients must not receive or need physical paths.

## B3 root-cause evidence

Reproduce and diagnose the append residue break path. Record an evidence chain
from observed behavior through mechanism to root cause, and add a regression
when repairable.

## B4 observability

Export bounded-cardinality `WORKTREE_DIRTY` rejection telemetry and integrate
the 15-minute continuous-rejection alert with the existing Prometheus,
Alertmanager and fleet-sentinel platform. Do not create a second stack. Validate
the rule with promtool and exercise the alert by inducing sustained rejection,
observing Alertmanager, then cleaning up.

## Required test modules and acceptance

The implementation must add direct behavioral tests, not source-text checks:

```bash
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_scope_guard.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_recovery.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_root_cause.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_dirty_observability.py
cd mcp/work-folder && uv run --extra dev pytest -q
```

Setup:

```bash
cd mcp/work-folder && uv sync --extra dev
```

The deploy-capable actor must record the established fleet-sentinel promtool
command and the complete live alert exercise as development evidence.
