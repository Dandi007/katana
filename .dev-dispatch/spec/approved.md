# Work-folder governance robustness: B1-B4

## Scope

Repair the governed work-folder write path without exposing the data repository
or physical paths to MCP clients. All production code and all code review are
performed by dev-dispatch actors only.

Target: `Dandi007/katana`, primarily `mcp/work-folder` and its governed-kernel
integration. Target baseline is the `main` commit recorded in
`.dev-dispatch/development.json`.

## Required behavior

### B1: Folder-scoped dirty isolation

Dirty state in `wf-X/` must not block a governed mutation scoped to `wf-Y/`.
Dirty state in the target folder or shared governance control files must remain
fail-closed. The existing `tests/test_scope_guard.py` is a required regression
suite; retain its byte-preservation and committed-diff assertions.

### B2: Governed residue recovery

For tracked, diffable, conflict-marker-free append-only residue in a folder's
accounting file, `wf_reconcile` must safely adopt the residue through the MCP
governance path instead of returning BROKEN. Existing content is preserved.
Provide an MCP-only manual recovery path with dry-run and per-file confirmation
for residue that cannot be auto-adopted. It must never require a client to
access the data repository or a physical path.

### B3: Broken-chain diagnosis

Determine and document the root cause of uncommitted append residue using a
reproducible evidence chain from observed behavior through the responsible
mechanism. Add a regression for the identified break path when it is fixable.
Write the conclusion and evidence locations to the development evidence, not to
an ungoverned client-side data repository.

### B4: Dirty-write observability

Expose a bounded-cardinality signal for `WORKTREE_DIRTY` rejections keyed by
folder-safe identity, and integrate the alert with the existing Prometheus,
Alertmanager, and fleet-sentinel platform. Do not create a second monitoring
stack. The alert threshold is 15 minutes of continuous rejection unless the
existing platform contract requires a stricter established threshold. Supply a
promtool-valid rule and an exercised alert path: induce sustained rejection and
prove the alert becomes visible within minutes.

## Safety constraints

- Preserve atomicity, CAS/concurrency protection, and auditability.
- Preserve all existing append-only user records; never discard residue to make
  a repository clean.
- Never weaken dirty checks outside the declared mutation scope.
- Client-visible results must not leak physical data-repository paths.
- Do not restart the production MCP service while a governed mutation is in
  flight. Service deployment is gated separately after tests and alert exercise.

## Required test contracts

Implement or extend these named test modules so each acceptance command is
directly discriminating rather than a source-text check:

1. `tests/test_scope_guard.py`: B1 sister-folder isolation and target/control
   rejection.
2. `tests/test_residue_recovery.py`: B2 auto-adoption, preservation, unsafe
   residue refusal, MCP-only dry-run and confirmed manual recovery.
3. `tests/test_residue_root_cause.py`: B3 reproduces the diagnosed append
   break path and protects its repair.
4. `tests/test_dirty_observability.py`: B4 metric emission and alert-rule
   contract. The platform exercise is additionally required as host verification.

## Acceptance commands

Run from the H0 worktree:

```bash
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_scope_guard.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_recovery.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_residue_root_cause.py
cd mcp/work-folder && uv run --extra dev pytest -q tests/test_dirty_observability.py
cd mcp/work-folder && uv run --extra dev pytest -q
```

## Setup commands

```bash
cd mcp/work-folder && uv sync --extra dev
```

## Host verification commands

The deploy-capable actor must replace placeholders only with the established
fleet-sentinel deployment paths discovered from the target environment, then
record complete stdout/stderr as development evidence:

```bash
promtool check rules <existing-fleet-sentinel-rule-file>
<existing-fleet-sentinel-alert-exercise-command-for-worktree-dirty>
```

The host exercise must prove: one folder has sustained `WORKTREE_DIRTY`
rejections, the threshold elapses, the alert is visible in the existing
Alertmanager path, and cleanup leaves no production residue.
