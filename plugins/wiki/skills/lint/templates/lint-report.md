# Lint Report — {{YYYY-MM-DD HH:MM}}

## Scope

- **Range:** {{full | zone:<name> | incremental}}
- **Run at:** {{YYYY-MM-DD HH:MM}}
- **Baseline:** {{last lint timestamp, or "first run — full"}}

## Mechanical findings

| type | page | detail |
|------|------|--------|
| orphan | `<page>` | no inlink anywhere |
| broken-link | `<page>` | `[[<target>]]` has no file |
| missing-frontmatter | `<page>` | missing `created` |
| index-drift | `<page>` | in index, file absent |
| naming | `<page>` | violates zone §2 rule |

## Semantic findings

### Contradiction pairs (antagonist rule — name, never smooth)

- **[[page-a]] ⟷ [[page-b]]** — disagreement: `<one sentence naming exactly what differs>`. → `> [!conflict]` callout proposed on both pages.

### Stale claims

- **[[page]]** — `<claim>` superseded by `<newer source>`; annotate stale, keep original.

### Missing pages (create candidates)

- `<concept>` — referenced by [[a]], [[b]], [[c]] (≥3), no page. → /wiki:ingest.

### Provenance gaps

- **[[page]]** — claim `<…>` has no backing source.

### Merge candidates

- **[[page-a]] + [[page-b]]** — `<why they overlap>`. Source: `<ingest log line | lint-found>`. → /wiki:ingest.

## Fix proposals

1. `<finding>` — zone `<z>`, policy `<propose|autonomous>`; fixer: **lint直接修 / 转 ingest / 人工**.
2. ...

## Skipped & exemptions

- `<page/path>` — skipped via §7 exemption `<rule>`.
