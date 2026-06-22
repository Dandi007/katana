"""Wiki ingest 两段式入库逻辑。

plan()  — orient 判重 + 返回判断指令脚手架（给模型据此造 proposal）。
apply() — 校验不变量 → 拒（零落盘）或写页 + 自动反链 + log + commit（原子）。

注入式依赖（validate_fn/write_fn/backlink_fn/log_fn/commit_fn）便于单测。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Knowledge constants — distilled from ingest skill references
# ---------------------------------------------------------------------------

CREATE_VS_UPDATE: str = (
    "Update first: if an existing page shares the same concrete-noun subject, "
    "update it and append the new source to frontmatter sources. "
    "Create only when ALL three hold: (1) ≥3 sentences of independent content, "
    "(2) concrete-noun test — nameable with one noun, "
    "(3) anti-cramming — folding into any existing page would give it two concepts. "
    "Merge candidates: log 'merge-candidate: A B' for /wiki:lint, don't merge mid-ingest. "
    "Skip pure duplicates: claim already cited from same source → log 'skipped: <unit>'."
)

RESIST_TABLE: str = (
    "Anti-laziness checklist (walk every row against your proposal): "
    "'Too small' → run the three-condition test, don't judge by feel. "
    "'Links next time' → linking is a write condition, not post-processing. "
    "'Too many back-updates' → touching N files is the LLM's advantage, skipping causes rot. "
    "'Summarize first' → summaries must carry claim-level anchors or provenance breaks. "
    "'Basically same' → write the difference into both pages and cross-annotate. "
    "'Direct write is faster' → bypasses all four invariants (provenance/linking/index/log). "
    "'Skip index update' → new page is invisible to query — it effectively doesn't exist."
)

UNIT_SPLITTING: str = (
    "One page = one concrete-noun concept. "
    "Split source into knowledge units before proposing pages. "
    "Each unit must pass the concrete-noun test independently. "
    "Units that share a subject go onto the same (existing or new) page — not separate stubs."
)

PROPOSAL_SCHEMA: str = (
    "proposal = {"
    "  'new_pages': [{"
    "    'path': str,                          # relative path under wiki_root, e.g. 'Zettelkasten/概念.md'"
    "    'frontmatter': {"
    "      '创建日期': 'YYYY-MM-DD HH:MM',"
    "      'tags': [str],"
    "      '类型': '卡片|索引|源码分析|架构',"
    "      'sources': [str],                   # required for all types in ingest mode"
    "      '摘要': str,                         # ≤40 chars, one-line summary"
    "      # optional: source_type, credibility (required pair for 源码分析/架构)"
    "    },"
    "    'body': str,                           # markdown body; must contain ≥1 [[wikilink]]"
    "    'back_updates': [{"
    "      'path': str,                         # existing page to receive backlink"
    "      'title': str                         # title of the new page"
    "    }]"
    "  }],"
    "  'log_line': str                          # one-line entry for log.md, e.g. '## [2026-06-22 10:00] ingest | source-name'"
    "}"
)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def plan(
    source_text: str,
    scope: str | None,
    *,
    search_fn,
) -> dict:
    """第一步：orient 判重检索 + 返回判断指令脚手架。

    Args:
        source_text: 待入库内容（或其摘录）。
        scope: 检索目录范围（None = 整库）。
        search_fn: vault_search.search 兼容签名。

    Returns:
        {
            "candidates": [{path, score, title, snippet}, ...],
            "instructions": {create_vs_update, resist_table, unit_splitting},
            "proposal_schema": PROPOSAL_SCHEMA,
        }
    """
    resp = search_fn(source_text[:200], top_k=10, dir=scope)
    candidates = [
        {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        for r in resp.results
    ]
    return {
        "candidates": candidates,
        "instructions": {
            "create_vs_update": CREATE_VS_UPDATE,
            "resist_table": RESIST_TABLE,
            "unit_splitting": UNIT_SPLITTING,
        },
        "proposal_schema": PROPOSAL_SCHEMA,
    }


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def apply(
    proposal: dict,
    wiki_root: str,
    *,
    validate_fn,
    write_fn,
    backlink_fn,
    log_fn,
    commit_fn,
    require_summary: bool = True,
    require_sources: bool = True,
) -> dict:
    """第二步：校验不变量 → 拒（零落盘）或写页 + 反链 + log + commit（原子）。

    Args:
        proposal: {new_pages: [...], log_line: str}
        wiki_root: wiki 根目录绝对路径。
        validate_fn: invariants.validate_page 兼容签名 (fm, body, *, require_summary, require_sources) -> list[str]
        write_fn: pages.write_page 兼容签名 (path, fm, body) -> None
        backlink_fn: pages.ensure_backlink 兼容签名 (path, title) -> bool
        log_fn: pages.append_log 兼容签名 (wiki_root, line) -> None
        commit_fn: pages.git_commit 兼容签名 (wiki_root, message, paths) -> str
        require_summary: 是否要求摘要（传给 validate_fn）。
        require_sources: 是否要求 frontmatter sources（传给 validate_fn）。

    Returns:
        成功: {"applied": True, "written": [paths], "backlinked": [bu_paths], "commit": sha}
        失败: {"applied": False, "rejected": {path: [errors]}}
    """
    from pathlib import Path

    new_pages: list[dict] = proposal.get("new_pages", [])
    log_line: str = proposal.get("log_line", "")

    # ---- Phase 1: validate ALL pages first (atomic: no partial writes) ----
    rejected: dict[str, list[str]] = {}
    for page in new_pages:
        fm = page.get("frontmatter", {})
        body = page.get("body", "")
        errs = validate_fn(fm, body, require_summary=require_summary, require_sources=require_sources)
        if errs:
            rejected[page["path"]] = errs

    if rejected:
        # Any error → reject everything, write nothing
        return {"applied": False, "rejected": rejected}

    # ---- Phase 2: all passed — write pages, backlinks, log, commit ----
    written: list[str] = []
    backlinked: list[str] = []

    for page in new_pages:
        abs_path = str(Path(wiki_root) / page["path"])
        write_fn(abs_path, page["frontmatter"], page["body"])
        written.append(page["path"])

    for page in new_pages:
        for bu in page.get("back_updates", []):
            bu_abs = str(Path(wiki_root) / bu["path"])
            backlink_fn(bu_abs, bu["title"])
            if bu["path"] not in backlinked:
                backlinked.append(bu["path"])

    log_fn(wiki_root, log_line)

    # Collect all paths for commit (new pages + back-updated pages + log.md)
    commit_paths = written + backlinked + ["log.md"]
    sha = commit_fn(wiki_root, "wiki: ingest", commit_paths)

    return {
        "applied": True,
        "written": written,
        "backlinked": backlinked,
        "commit": sha,
    }
