# Work-folder governance robustness B1-B4: replacement after failed seal

All product implementation and code review are dev-dispatch-only. Preserve atomicity, CAS, auditability, append-only records, and the Zero-mount invariant.

## Immutable prior evidence

The predecessor `dev_katana_wf_governance_b1_b4_20260827_02` failed at deterministic seal, not review, with:

```text
UNVERIFIED_TEST_CLAIM: ["uv","run","--extra","dev","pytest","-q","tests/test_scope_guard.py"] claimed exit 0 but real exit is 4
```

It produced no accepted candidate, no Implement receipt, no review, and no acceptance. Its local-only unaccepted commits were `2cae914` and `812d46f`; do not treat either as an accepted handoff or copy a claimed test result from them. The replacement must independently recreate only correct product behavior from this specification, then report actual final-tree command outcomes. Before returning APPLIED, run the exact five acceptance argv entries at the final clean commit after setup; record their real exits only. A clean final worktree is mandatory.

## B1 scope isolation

Dirty `wf-X/` must not block a governed mutation in `wf-Y/`; dirt in the target folder or shared governance controls remains fail-closed. Retain direct byte-preservation and committed-diff coverage in `tests/test_scope_guard.py`.

## B2 governed recovery

`wf_reconcile` must automatically adopt tracked, diffable, conflict-free, append-only accounting residue for its folder, preserving data. Unsafe residue remains refused. Provide an MCP-only manual recovery surface with dry-run and per-file confirmation; no client-visible physical repository path is allowed.

## B3 findings are required evidence

Identify the real residue break mechanism, not merely a synthetic append. Add a regression when repairable and write a durable, reviewable findings artifact that closes: observed incident shape -> responsible mechanism -> reproducer or repository evidence -> repair. The final evidence must cite this artifact.

## B4 deployment and live drill are required evidence

Export bounded-cardinality dirty-rejection telemetry and integrate a 15-minute continuous rejection alert with the existing Prometheus, Alertmanager and fleet-sentinel platform. Do not add another monitoring stack. A YAML string assertion is insufficient. Before acceptance, the deploy-capable actor must:

1. run `promtool check rules` on the deployed rule source;
2. deploy through the established fleet-sentinel path;
3. induce sustained `WORKTREE_DIRTY` rejection for the full threshold;
4. capture Alertmanager-visible alert evidence; and
5. clean the drill residue and record the cleanup result.

The commands, timestamps, and unredacted-for-engine raw outputs must be present in the receipt/evidence chain. If platform access is unavailable, report BLOCKED with the exact external blocker; never claim B4 complete.

## Frozen setup and acceptance

```bash
cd mcp/work-folder && uv sync --extra dev
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_scope_guard.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_recovery.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_root_cause.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_dirty_observability.py
cd mcp/work-folder && uv run --extra dev pytest -q
```
