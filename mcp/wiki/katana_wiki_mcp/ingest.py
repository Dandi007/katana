"""Wiki ingest 两段式入库逻辑。

plan()  — orient 判重 + 返回判断指令脚手架（给模型据此造 proposal）。
apply() — 校验不变量 → 拒（零落盘）或写页 + 自动反链 + log + commit（原子）。

注入式依赖（validate_fn/write_fn/backlink_fn/log_fn/commit_fn）便于单测。
"""

from __future__ import annotations

import copy
import re
import unicodedata
from pathlib import Path, PurePosixPath

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
    "Call wiki_ingest_apply(proposal, expected_base_sha=plan.base_sha) whenever updates is non-empty. "
    "proposal = {"
    "  'new_pages': [{"
    "    'path': str,                          # canonical POSIX relative path; ':' allowed except Windows drive prefix"
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
    "  'updates': [{"
    "    'path': str,                          # existing path; updates never move pages"
    "    'frontmatter': {"
    "      'id': str,                           # conditional: preserve existing id; omit when legacy page has no id"
    "      '创建日期': 'YYYY-MM-DD HH:MM',"
    "      'tags': [str],"
    "      '类型': '卡片|索引|源码分析|架构',"
    "      'sources': [str],"
    "      '摘要': str"
    "    },"
    "    'body': str,"
    "    'back_updates': [{"
    "      'path': str,"
    "      'title': str"
    "    }]"
    "  }],"
    "  'log_line': str                          # one-line entry for log.md, e.g. '## [2026-06-22 10:00] ingest | source-name'"
    "}"
)

_EXCLUDED_PAGE_ZONES = {
    ".git", ".katana", ".obsidian", ".wiki", ".trash",
    "_quarantine", "raw", "inbox", "转换文档", "DeepThought",
}
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_governed_writable_path(canonical_path: str) -> bool:
    parts = PurePosixPath(canonical_path).parts
    return (
        bool(parts)
        and not any(part.startswith(".") for part in parts)
        and not any(part in _EXCLUDED_PAGE_ZONES for part in parts)
    )


def _canonical_path(wiki_root: str, raw_path: object) -> tuple[str | None, str | None]:
    """Return a canonical POSIX relative path confined under wiki_root."""
    if not isinstance(raw_path, str) or not raw_path:
        return None, "path must be a non-empty string"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_path):
        return None, "C0 control characters and DEL are not allowed in paths"
    if (
        "\\" in raw_path
        or raw_path.startswith("//")
        or _WINDOWS_DRIVE_PATH_RE.match(raw_path)
    ):
        return None, "Windows drive/root/UNC paths are not allowed; use POSIX relative paths"
    raw_path = unicodedata.normalize("NFC", raw_path)
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts:
        return None, "path must be relative and stay inside wiki_root"
    parts = [part for part in pure.parts if part not in ("", ".")]
    if not parts:
        return None, "path must not resolve to wiki_root"

    root = Path(wiki_root).resolve()
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, f"path lstat failed: {exc.__class__.__name__}"
        if current.is_symlink():
            return None, "symlink paths are not allowed"
        if index < len(parts) - 1 and not current.is_dir():
            return None, "existing path ancestor must be a non-symlink directory"
    try:
        current.resolve(strict=False).relative_to(root)
    except ValueError:
        return None, "path escapes wiki_root"
    return PurePosixPath(*parts).as_posix(), None


def _validate_existing_wiki_page(
    wiki_root: str, canonical_path: str, *, read_fn=None
) -> str | None:
    """Backlink targets must be governed, parseable regular Markdown pages."""
    pure = PurePosixPath(canonical_path)
    if pure.suffix != ".md":
        return "back_update target must be a regular .md wiki page"
    if any(part.startswith(".") for part in pure.parts):
        return "back_update target dotfiles are not governed wiki pages"
    if not is_governed_writable_path(canonical_path):
        return "back_update target is in raw/excluded wiki zone"
    target = Path(wiki_root) / canonical_path
    if target.is_symlink() or not target.is_file():
        return "back_update target must be a non-symlink regular .md wiki page"
    try:
        text = target.read_text(encoding="utf-8")
        if not text.startswith("---\n") or text[4:].find("\n---\n") < 0:
            return "back_update target requires paired frontmatter delimiters"
        if read_fn is None:
            from katana_wiki_mcp.pages import parse_page
            frontmatter, _ = parse_page(text)
        else:
            frontmatter, _ = read_fn(str(target))
    except Exception as exc:
        return f"back_update target frontmatter parse failed: {exc.__class__.__name__}"
    if not isinstance(frontmatter, dict):
        return "back_update target frontmatter must be a mapping"
    from katana_wiki_mcp.invariants import check_frontmatter
    errors = check_frontmatter(frontmatter)
    if errors:
        return "back_update target frontmatter invalid: " + "; ".join(errors)
    return None


def normalize_proposal(
    proposal: object, wiki_root: str, *, read_fn=None
) -> tuple[dict, dict[str, list[str]]]:
    """Canonicalize and structurally validate a proposal before any mutation."""
    if not isinstance(proposal, dict):
        return {}, {"(proposal)": ["proposal must be an object"]}
    normalized = copy.deepcopy(proposal)
    rejected: dict[str, list[str]] = {}

    log_line = normalized.get("log_line", "")
    if not isinstance(log_line, str):
        rejected.setdefault("(proposal)", []).append("log_line must be a string")

    all_pages: list[tuple[str, dict]] = []
    for kind in ("new_pages", "updates"):
        pages = normalized.get(kind, [])
        if not isinstance(pages, list):
            rejected.setdefault("(proposal)", []).append(f"{kind} must be a list")
            continue
        for page in pages:
            if not isinstance(page, dict):
                rejected.setdefault("(proposal)", []).append(
                    f"{kind} entries must be objects"
                )
                continue
            canonical, error = _canonical_path(wiki_root, page.get("path"))
            error_key = str(page.get("path") or "(proposal)")
            if error:
                rejected.setdefault(error_key, []).append(error)
                continue
            page["path"] = canonical
            if not is_governed_writable_path(canonical):
                rejected.setdefault(canonical, []).append(
                    "proposal pages must stay in governed writable wiki zones"
                )
            if PurePosixPath(canonical).suffix != ".md":
                rejected.setdefault(canonical, []).append(
                    "proposal page path must end with .md"
                )
            if not isinstance(page.get("frontmatter"), dict):
                rejected.setdefault(canonical, []).append(
                    "frontmatter must be an object"
                )
            if not isinstance(page.get("body"), str):
                rejected.setdefault(canonical, []).append("body must be a string")

            back_updates = page.get("back_updates", [])
            if not isinstance(back_updates, list):
                rejected.setdefault(canonical, []).append(
                    "back_updates must be a list"
                )
            else:
                expected_title = PurePosixPath(canonical).stem
                for back_update in back_updates:
                    if not isinstance(back_update, dict):
                        rejected.setdefault(canonical, []).append(
                            "back_updates entries must be objects"
                        )
                        continue
                    back_path, back_error = _canonical_path(
                        wiki_root, back_update.get("path")
                    )
                    if back_error:
                        rejected.setdefault(canonical, []).append(
                            f"back_update {back_error}"
                        )
                        continue
                    back_update["path"] = back_path
                    target_error = _validate_existing_wiki_page(
                        wiki_root, back_path, read_fn=read_fn
                    )
                    if target_error:
                        rejected.setdefault(canonical, []).append(target_error)
                    title = back_update.get("title")
                    if not isinstance(title, str) or title != expected_title:
                        rejected.setdefault(canonical, []).append(
                            f"back_update title must equal proposal page title {expected_title!r}"
                        )
            all_pages.append((kind, page))

    if not normalized.get("new_pages") and not normalized.get("updates"):
        rejected.setdefault("(proposal)", []).append(
            "new_pages and updates are both empty"
        )

    seen: set[str] = set()
    for _, page in all_pages:
        path = page.get("path")
        if not isinstance(path, str):
            continue
        if path in seen:
            rejected.setdefault(path, []).append(
                "同一路径在 proposal 中重复或同时出现在 create/update"
            )
        seen.add(path)
    return normalized, rejected


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def plan(
    source_text: str,
    scope: str | None,
    *,
    search_fn,
    base_sha: str | None = None,
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
            "base_sha": str,  # updates apply 必须作为 expected_base_sha 原样回传
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
        "base_sha": base_sha or "",
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
    read_fn=None,
    backlink_fn,
    log_fn,
    commit_fn,
    expected_base_sha: str | None = None,
    base_sha_fn=None,
    require_summary: bool = True,
    require_sources: bool = True,
) -> dict:
    """第二步：校验不变量 → 拒（零落盘）或写页 + 反链 + log + commit（原子）。

    Args:
        proposal: {new_pages: [...], updates: [...], log_line: str}
        wiki_root: wiki 根目录绝对路径。
        validate_fn: invariants.validate_page 兼容签名 (fm, body, *, require_summary, require_sources) -> list[str]
        write_fn: pages.write_page 兼容签名 (path, fm, body) -> None
        read_fn: pages.read_page 兼容签名 (path) -> (frontmatter, body)。
        backlink_fn: pages.ensure_backlink 兼容签名 (path, title) -> bool
        log_fn: pages.append_log 兼容签名 (wiki_root, line) -> None
        commit_fn: pages.git_commit 兼容签名 (wiki_root, message, paths) -> str
        require_summary: 是否要求摘要（传给 validate_fn）。
        require_sources: 是否要求 frontmatter sources（传给 validate_fn）。

    Returns:
        成功: {"applied": True, "written": [paths], "backlinked": [bu_paths], "commit": sha}
        失败: {"applied": False, "rejected": {path: [errors]}}
    """
    if read_fn is None:
        from katana_wiki_mcp.pages import read_page
        read_fn = read_page
    if base_sha_fn is None:
        from katana_kernel.gitops import head_sha
        base_sha_fn = head_sha

    normalized, rejected = normalize_proposal(
        proposal, wiki_root, read_fn=read_fn
    )
    new_pages = normalized.get("new_pages", [])
    updates = normalized.get("updates", [])
    log_line = normalized.get("log_line", "")

    if updates and not expected_base_sha:
        rejected.setdefault("(proposal)", []).append(
            "updates require expected_base_sha from wiki_ingest_plan"
        )
    if expected_base_sha is not None and (
        not isinstance(expected_base_sha, str) or len(expected_base_sha) != 40
    ):
        rejected.setdefault("(proposal)", []).append(
            "expected_base_sha must be a 40-character commit SHA"
        )
    elif expected_base_sha and base_sha_fn(wiki_root) != expected_base_sha:
        rejected.setdefault("(proposal)", []).append("CAS base SHA mismatch")

    valid_pages = [
        *(new_pages if isinstance(new_pages, list) else []),
        *(updates if isinstance(updates, list) else []),
    ]
    for kind, pages in (("new_pages", new_pages), ("updates", updates)):
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, dict):
                continue
            path = page.get("path")
            fm = page.get("frontmatter")
            body = page.get("body")
            if not isinstance(path, str) or not isinstance(fm, dict) or not isinstance(body, str):
                continue
            errs = validate_fn(
                fm, body,
                require_summary=require_summary,
                require_sources=require_sources,
            )
            if kind == "new_pages" and fm.get("id"):
                errs.append("new_pages 不得指定 id；existing page 必须走 updates")
            if errs:
                rejected.setdefault(path, []).extend(errs)

    id_paths: dict[str, list[str]] = {}
    nfc_paths: dict[str, list[str]] = {}
    for existing in Path(wiki_root).rglob("*.md"):
        if existing.is_symlink() or not existing.is_file():
            continue
        relative_path = existing.relative_to(Path(wiki_root)).as_posix()
        normalized_existing_path = unicodedata.normalize("NFC", relative_path)
        if is_governed_writable_path(normalized_existing_path):
            nfc_paths.setdefault(normalized_existing_path, []).append(relative_path)
        try:
            fm, _ = read_fn(str(existing))
        except Exception:
            continue
        if (
            is_governed_writable_path(normalized_existing_path)
            and isinstance(fm, dict)
            and fm.get("id")
        ):
            id_paths.setdefault(str(fm["id"]), []).append(
                relative_path
            )
    duplicates = {pid: paths for pid, paths in id_paths.items() if len(paths) > 1}
    if duplicates:
        rejected.setdefault("(proposal)", []).append(
            f"existing duplicate ids: {duplicates}"
        )
    path_collisions = {
        path: actuals for path, actuals in nfc_paths.items() if len(actuals) > 1
    }
    if path_collisions:
        rejected.setdefault("(proposal)", []).append(
            f"existing NFC path collisions: {path_collisions}"
        )

    for page in new_pages if isinstance(new_pages, list) else []:
        if isinstance(page, dict) and isinstance(page.get("path"), str):
            exact_path = Path(wiki_root) / page["path"]
            try:
                exact_path.lstat()
                exact_exists = True
            except FileNotFoundError:
                exact_exists = False
            except OSError as exc:
                rejected.setdefault(page["path"], []).append(
                    f"exact path lstat failed: {exc.__class__.__name__}"
                )
                exact_exists = False
            if exact_exists or page["path"] in nfc_paths:
                rejected.setdefault(page["path"], []).append(
                    "new_pages path 已存在；existing page 必须走 updates"
                )

    for page in updates if isinstance(updates, list) else []:
        if not isinstance(page, dict) or not isinstance(page.get("path"), str):
            continue
        exact_path = Path(wiki_root) / page["path"]
        try:
            exact_path.lstat()
            exact_exists = True
        except FileNotFoundError:
            exact_exists = False
        except OSError as exc:
            rejected.setdefault(page["path"], []).append(
                f"exact path lstat failed: {exc.__class__.__name__}"
            )
            exact_exists = False
        actual_paths = nfc_paths.get(page["path"], [])
        if not exact_exists or not actual_paths:
            rejected.setdefault(page["path"], []).append(
                "update path 不存在；新页面必须走 new_pages"
            )
            continue
        if len(actual_paths) != 1 or actual_paths[0] != page["path"]:
            rejected.setdefault(page["path"], []).append(
                "update target path is not uniquely NFC canonical"
            )
            continue
        abs_path = Path(wiki_root) / page["path"]
        try:
            current_fm, _ = read_fn(str(abs_path))
        except Exception as exc:
            rejected.setdefault(page["path"], []).append(
                f"existing page frontmatter 解析失败: {exc.__class__.__name__}"
            )
            continue
        if not isinstance(current_fm, dict):
            rejected.setdefault(page["path"], []).append(
                "existing page frontmatter must be a mapping"
            )
            continue
        proposed_fm = page.get("frontmatter") or {}
        current_id = current_fm.get("id")
        proposed_id = proposed_fm.get("id")
        if not current_id:
            if proposed_id:
                rejected.setdefault(page["path"], []).append(
                    "legacy page has no id; update must not add or forge id"
                )
        elif current_id != proposed_id:
            rejected.setdefault(page["path"], []).append(
                f"update id/path mismatch：path 当前 id={current_id!r}"
            )
        elif len(id_paths.get(str(current_id), [])) != 1:
            rejected.setdefault(page["path"], []).append(
                "update target id is not unique"
            )
        old_sources = current_fm.get("sources", [])
        new_sources = proposed_fm.get("sources", [])
        if (
            not isinstance(old_sources, list)
            or not isinstance(new_sources, list)
            or not all(isinstance(item, str) for item in old_sources)
            or not all(isinstance(item, str) for item in new_sources)
            or not set(old_sources).issubset(set(new_sources))
        ):
            rejected.setdefault(page["path"], []).append(
                "update sources must be a superset of existing sources"
            )

    for page in valid_pages:
        if not isinstance(page, dict):
            continue
        for back_update in page.get("back_updates") or []:
            if isinstance(back_update, dict) and isinstance(back_update.get("path"), str):
                if not (Path(wiki_root) / back_update["path"]).exists():
                    rejected.setdefault(page.get("path", "(proposal)"), []).append(
                        f"back_update path 不存在: {back_update['path']}"
                    )

    if rejected:
        # Any error → reject everything, write nothing
        return {"applied": False, "rejected": rejected}

    # ---- Phase 2: all passed — write pages, backlinks, log, commit ----
    written: list[str] = []
    created: list[str] = []
    updated: list[str] = []
    backlinked: list[str] = []

    for page in new_pages:
        abs_path = str(Path(wiki_root) / page["path"])
        write_fn(abs_path, page["frontmatter"], page["body"])
        written.append(page["path"])
        created.append(page["path"])

    for page in updates:
        abs_path = str(Path(wiki_root) / page["path"])
        write_fn(abs_path, page["frontmatter"], page["body"])
        written.append(page["path"])
        updated.append(page["path"])

    for page in [*new_pages, *updates]:
        for bu in page.get("back_updates", []):
            bu_abs = str(Path(wiki_root) / bu["path"])
            backlink_fn(bu_abs, bu["title"])
            if bu["path"] not in backlinked:
                backlinked.append(bu["path"])

    if log_line:
        log_fn(wiki_root, log_line)

    # Collect all paths for commit (new pages + back-updated pages + log.md)
    commit_paths = written + backlinked + (["log.md"] if log_line else [])
    sha = commit_fn(wiki_root, "wiki: ingest", commit_paths)

    return {
        "applied": True,
        "written": written,
        "created": created,
        "updated": updated,
        "backlinked": backlinked,
        "commit": sha,
    }
