# Create vs Update

Run every knowledge unit through this in order. The bias is **update first** —
new pages are the exception, not the default. Spawning a page for every fragment
is how an AI-maintained wiki bloats into mediocrity.

## 1. Update first

If an existing page can absorb the unit — same **concrete-noun** subject — update
that page and **append the new source** to its frontmatter `sources:`. Do not
create.

> Example: source has a new caveat about `retry-backoff`; a `retry-backoff` page
> already exists → add the caveat there, append the source.

## 2. Create — only when ALL three hold

1. **≥3 sentences** of independent content — less is a paragraph on an existing page.
2. **Concrete-noun test** — you can name it with one concrete noun. If you can't, it's not a page.
3. **Anti-cramming** — folding it into any existing page would make that page hold more than one concept.

> Example: source introduces `circuit-breaker`, 5 sentences, fits no existing
> page without giving that page a second concept → create `circuit-breaker`.

## 3. Merge candidate

If you find **two existing pages covering the same thing**, do **not** merge them
mid-ingest. Record it in `log.md`: `merge-candidate: <page A> <page B>` and leave
it for `/wiki:lint`.

> Example: `retry-backoff` and `backoff-retry` both exist → log a merge-candidate, move on.

## 4. Skip

If the unit is a **pure duplicate** — the claim is already on a page **and** that
source is already recorded — skip it, but record `skipped: <unit>` in the log so
the decision is auditable.

> Example: source repeats a claim an existing page already cites from this same
> source → skip, log it.
