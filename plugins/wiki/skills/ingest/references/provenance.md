# Provenance

Every claim in the wiki must trace back to a source. Provenance is the spine of
the whole system — without it, the wiki drifts into ungrounded assertion and
model collapse. This is how ingest records it.

## Source types and citation format

- **Local file** — path relative to `wiki_root`, with an optional line anchor for
  the exact span: `notes/raw/spec.md` or `notes/raw/spec.md#L12-L40`.
- **URL** — **never cite a live URL directly**. Fetch and archive into the raw
  layer first (SKILL.md §2), then cite the **raw copy path + original URL +
  fetch date**: `raw/2026-06-05-acme-docs.html (https://acme.example/docs, fetched 2026-06-05)`.
  Live pages are mutable; the raw copy is immutable.
- **Conversation capture** — `conversation 2026-06-05` plus a one-line context note
  of what was discussed.

## frontmatter `sources:`

A YAML list, one entry per source:

```yaml
sources:
  - notes/raw/spec.md#L12-L40
  - raw/2026-06-05-acme-docs.html (https://acme.example/docs, fetched 2026-06-05)
  - conversation 2026-06-05 — decided retry cap is 5
```

## Claim-level citation

Important claims inside a page link back to their specific source via an inline
marker or footnote — not just the frontmatter list. A claim synthesized from
multiple sources **lists all** supporting sources at that claim.

## Iron rules (restated)

- **Raw is immutable.** Never edit an archived source in place.
- **Never ingest your own un-reviewed composite output.** Synthesis no human has
  reviewed is not a citable source — feeding it back is the model-collapse path.
