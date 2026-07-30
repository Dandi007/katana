# Wiki v2 MCP Test Report

- branch: dev_wiki_v2_01 @ `REWORK`
- date: 2026-07-30
- **PASS 70 / FAIL 0 / SKIP 0**

| area | tests | result |
|---|---|---|
| A1 Write Three-State | 12 | PASS |
| A2 Rename Regression | 4 | PASS |
| A3 Delete Regression | 3 | PASS |
| A4 Invariants (INV-1, INV-2) | 4 | PASS |
| A5 Search (hybrid/error) | 4 | PASS |
| A6 Bad Page Isolation | 2 | PASS |
| A7 Migration CLI | 7 | PASS |
| A8 Concurrency | 1 | PASS |
| Pages Unit | 10 | PASS |
| Invariants Unit | 4 | PASS |
| Query Unit | 2 | PASS |
| VFS Unit | 4 | PASS |
| Store Read | 6 | PASS |
| Meta Write | 2 | PASS |
| Report Gap | 1 | PASS |
| Ingest Plan/Apply | 2 | PASS |
| Rebuild Index | 1 | PASS |
| INV-5 Clean Worktree | 1 | PASS |

## Acceptance Criteria Coverage

- [x] A1 — Each write tool three-state: validation reject / success / manifest+commit
- [x] A2 — Rename regression: A←B/C links, rewrite, NOT_FOUND, broken link count = 0
- [x] A3 — Delete regression: inlink blocking, force+remove_links, single commit
- [x] A4 — INV-1: no mutating fs_* tools. INV-2: id server-issued, immutable, falsification rejected
- [x] A5 — Search: fake embedder hybrid mode; error embedder keyword_only + last_error
- [x] A6 — INV-6: bad frontmatter page isolated, other ops unaffected
- [x] A7 — Migration: flat structure, link normalization, id preservation/power, excluded dirs, conflict detection, idempotent
- [x] A8 — Concurrent writes serialized, no cross-contamination

## Test Execution

```
cd mcp/wiki-v2 && uv run --extra dev pytest -q
70 passed in 5.59s
```