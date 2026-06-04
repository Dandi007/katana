# Resist Table

Anti-laziness checklist. Each row pairs an excuse you might reach for with the
reason it fails. Walk every row against your proposal (SKILL.md §6). These map
directly to the failure modes the pipeline exists to prevent — if an excuse feels
reasonable, that is exactly when to distrust it.

| Excuse | Rebuttal |
|--------|----------|
| "This unit is too small to deserve a page" | Run the create-vs-update three-condition test, don't judge by feel — ≥3 sentences already qualifies. |
| "I'll add the links next time" | Mandatory linking is a write condition, not post-processing. An unlinked page is an orphan, and lint will catch it. |
| "Too many related pages to back-update" | Touching N files in one pass is precisely the LLM's comparative advantage. Skipping it is exactly why human-maintained wikis rot. |
| "The source is long, I'll summarize it first" | A summary must carry claim-level anchors back to the source, or provenance breaks. Summarize with anchors or not at all. |
| "This is basically the same as an existing page" | Basically-the-same ≠ the-same. Write the difference into both pages and cross-annotate them (the antagonist spirit). |
| "Direct Write is faster" | Bypassing the pipeline breaks all four invariants at once: provenance, linking, index, and log. Speed here is debt. |
| "I'll skip the index update this round" | The index is query's first hop. Not updating it means the new page is invisible to query — it effectively doesn't exist. |
