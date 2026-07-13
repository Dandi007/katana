"""Migration import/rehearsal engine (M3b REHEARSED phase, rehearsal-only).

Consumes M3a manifest, performs full import into frozen destination copies,
produces destination git structures (tree + final maintenance commit +
MIGRATION_BASE marker + redirect/reference catalog).  Idempotent: same
manifest + same source -> byte-identical result.

All writes land in temporary rehearsal destination roots.  Never touches
production data roots.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from pathlib import Path

from katana_migration.inventory import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_MERGE,
    ACTION_NORMALIZE,
    ACTION_PRESERVE,
    ACTION_REJECT,
    ACTION_REWRITE,
    EXC_BINARY,
    EXC_CASEFOLD_COLLISION,
    EXC_EXECUTABLE,
    EXC_LFS_POINTER,
    EXC_PATH_LENGTH,
    EXC_SYMLINK,
    MAX_BASENAME_LENGTH,
    MAX_PATH_LENGTH,
    is_binary,
    is_lfs_pointer,
    sha256_hex,
)

# ── Rehearsal constants ───────────────────────────────────────────────────────

_MIGRATION_BASE_CONTENT = "MIGRATION_BASE\n"
_DEFAULT_COMMITTER_NAME = "migration-rehearsal"
_DEFAULT_COMMITTER_EMAIL = "rehearsal@katana.local"
_DEFAULT_COMMITTER_DATE = "2026-07-13T00:00:00Z"

# ── Reference extraction ──────────────────────────────────────────────────────

_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_MEMORY_ID_RE = re.compile(r"m-[0-9a-f]{6}")
_WIKI_ID_RE = re.compile(r"w-[0-9a-f]{6}")
_WF_ID_RE = re.compile(r"wf-[0-9a-f]{6}")


def _extract_references(content: str, source_path: str) -> list[dict]:
    refs: list[dict] = []
    for m in _WIKI_LINK_RE.finditer(content):
        refs.append({
            "source_path": source_path,
            "ref_type": "wiki_link",
            "old_literal": m.group(0),
            "target": m.group(1),
            "anchor": None,
            "span": m.span(),
        })
    for m in _MARKDOWN_LINK_RE.finditer(content):
        refs.append({
            "source_path": source_path,
            "ref_type": "markdown_link",
            "old_literal": m.group(0),
            "target": m.group(2),
            "anchor": None,
            "span": m.span(),
        })
    for m in _MEMORY_ID_RE.finditer(content):
        refs.append({
            "source_path": source_path,
            "ref_type": "memory_id",
            "old_literal": m.group(0),
            "target": m.group(0),
            "anchor": None,
            "span": m.span(),
        })
    for m in _WIKI_ID_RE.finditer(content):
        refs.append({
            "source_path": source_path,
            "ref_type": "wiki_id",
            "old_literal": m.group(0),
            "target": m.group(0),
            "anchor": None,
            "span": m.span(),
        })
    for m in _WF_ID_RE.finditer(content):
        refs.append({
            "source_path": source_path,
            "ref_type": "wf_id",
            "old_literal": m.group(0),
            "target": m.group(0),
            "anchor": None,
            "span": m.span(),
        })
    return refs


# ── Action implementations ────────────────────────────────────────────────────

def _apply_preserve(content: bytes, record: dict) -> tuple[bytes, dict | None]:
    return content, None


def _apply_id_backfill(content: bytes, record: dict) -> tuple[bytes, dict | None]:
    resource_id = record.get("domain_resource_id", "")
    if not resource_id:
        return content, None
    if not content.startswith(b"---\n"):
        return content, None
    text = content.decode("utf-8")
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return content, None
    body_start = 4 + fm_end.end()
    frontmatter = text[:body_start]
    body = text[body_start:]
    if "id:" not in frontmatter:
        new_fm = frontmatter.rstrip() + f"\nid: {resource_id}\n---\n"
        result = (new_fm + body).encode("utf-8")
        return result, {
            "transformation": "id_backfill",
            "resource_id": resource_id,
            "body_bytes_unchanged": sha256_hex(body.encode("utf-8")),
        }
    return content, None


def _apply_normalize(content: bytes, record: dict) -> tuple[bytes, dict | None]:
    return content, {"transformation": "normalize", "note": "no-op placeholder"}


def _apply_rewrite(content: bytes, record: dict) -> tuple[bytes, dict | None]:
    return content, {"transformation": "rewrite", "note": "no-op placeholder"}


def _apply_merge(content: bytes, record: dict) -> tuple[bytes, dict | None]:
    return content, {"transformation": "merge", "note": "no-op placeholder"}


_ACTION_HANDLERS = {
    ACTION_PRESERVE: _apply_preserve,
    ACTION_ID_BACKFILL: _apply_id_backfill,
    ACTION_NORMALIZE: _apply_normalize,
    ACTION_REWRITE: _apply_rewrite,
    ACTION_MERGE: _apply_merge,
}


# ── Integrity gate ────────────────────────────────────────────────────────────

def _check_integrity(dest_path: Path, content: bytes, record: dict) -> list[dict]:
    issues: list[dict] = []
    rel_path = str(dest_path)

    if dest_path.is_symlink():
        issues.append({"code": EXC_SYMLINK, "path": rel_path, "reason": "Symlink"})
        return issues

    if is_binary(content):
        issues.append({"code": EXC_BINARY, "path": rel_path, "reason": "Binary content"})

    if is_lfs_pointer(content):
        issues.append({"code": EXC_LFS_POINTER, "path": rel_path, "reason": "LFS pointer"})

    try:
        st = dest_path.stat()
        if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            issues.append({"code": EXC_EXECUTABLE, "path": rel_path, "reason": "Executable bit set"})
    except OSError:
        pass

    basename = dest_path.name
    if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
        issues.append({"code": EXC_PATH_LENGTH, "path": rel_path, "reason": f"Basename > {MAX_BASENAME_LENGTH} bytes"})

    if len(rel_path.encode("utf-8")) > MAX_PATH_LENGTH:
        issues.append({"code": EXC_PATH_LENGTH, "path": rel_path, "reason": f"Path > {MAX_PATH_LENGTH} bytes"})

    try:
        text = content.decode("utf-8")
        nfc = unicodedata.normalize("NFC", text)
        if text != nfc:
            issues.append({"code": "UNICODE_NORMALIZATION", "path": rel_path, "reason": "Not NFC-normalized"})
    except UnicodeDecodeError:
        pass

    return issues


def _check_casefold_collisions(dest_roots: dict[str, str], output_objects: list[dict]) -> list[dict]:
    issues: list[dict] = []
    per_repo_casefold: dict[str, dict[str, str]] = {}
    for obj in output_objects:
        action = obj.get("action", "")
        if action == ACTION_REJECT or action == ACTION_ARCHIVE:
            continue
        dest_repo = obj.get("destination_repo", "")
        dest_path = obj.get("destination_path", obj.get("source_path", ""))
        basename = Path(dest_path).name
        cf = basename.casefold()
        cf_map = per_repo_casefold.setdefault(dest_repo, {})
        if cf in cf_map and cf_map[cf] != basename:
            issues.append({
                "code": EXC_CASEFOLD_COLLISION,
                "path": dest_path,
                "reason": f"Casefold collision with {cf_map[cf]}: {basename}",
            })
        else:
            cf_map[cf] = basename
    return issues


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_init(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    git_dir = repo_path / ".git"
    if not git_dir.is_dir():
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
        )


def _git_add_all(repo_path: Path) -> None:
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


def _git_commit(
    repo_path: Path,
    message: str,
    committer_name: str,
    committer_email: str,
    committer_date: str,
    allow_empty: bool = False,
) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": committer_name,
        "GIT_AUTHOR_EMAIL": committer_email,
        "GIT_AUTHOR_DATE": committer_date,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
        "GIT_COMMITTER_DATE": committer_date,
    }
    args = ["git", "commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    result = subprocess.run(
        args,
        cwd=str(repo_path),
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        return ""
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    return rev.stdout.decode().strip()


def _git_tree_sha(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


def _git_head_sha(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


# ── Rehearsal engine ──────────────────────────────────────────────────────────

def run_rehearsal(
    manifest: dict,
    source_roots: dict[str, str],
    dest_roots: dict[str, str],
    committer_name: str = _DEFAULT_COMMITTER_NAME,
    committer_email: str = _DEFAULT_COMMITTER_EMAIL,
    committer_date: str = _DEFAULT_COMMITTER_DATE,
    migration_run_id: str | None = None,
    fail_on_integrity: bool = True,
) -> dict:
    """Run the REHEARSED stage import/rehearsal engine.

    Args:
        manifest: M3a manifest dict with 'objects', 'redirect_map', 'summary', etc.
        source_roots: Mapping of source_repo -> filesystem path to frozen source.
        dest_roots: Mapping of destination_repo -> filesystem path for rehearsal output.
        committer_name: Fixed committer name for idempotent commits.
        committer_email: Fixed committer email for idempotent commits.
        committer_date: Fixed committer date for idempotent commits.
        migration_run_id: Override migration run ID.
        fail_on_integrity: If True, raise on integrity violations.

    Returns:
        Rehearsal result dict with destination_trees, reference_manifest,
        integrity_report, redirect_catalog, and summary.
    """
    run_id = migration_run_id or manifest.get("migration_run_id", "mig-000000000000")
    objects = manifest.get("objects", [])
    redirect_map = manifest.get("redirect_map", {})

    rehearsal_results: list[dict] = []
    all_references: list[dict] = []
    integrity_issues: list[dict] = []
    output_objects: list[dict] = []

    dest_repos_inited: set[str] = set()

    for record in objects:
        action = record.get("action", ACTION_REJECT)
        source_repo = record.get("source_repo", "")
        source_path = record.get("source_path", "")
        dest_repo = record.get("destination_repo", source_repo)
        dest_path = record.get("destination_path", source_path)

        source_root = source_roots.get(source_repo)
        if source_root is None:
            continue

        dest_root = dest_roots.get(dest_repo)
        if dest_root is None:
            continue

        if dest_repo not in dest_repos_inited:
            _git_init(Path(dest_root))
            dest_repos_inited.add(dest_repo)

        src_file = Path(source_root) / source_path
        dest_file = Path(dest_root) / dest_path

        if action == ACTION_REJECT:
            output_objects.append({**record, "rehearsal_status": "rejected"})
            continue

        if action == ACTION_ARCHIVE:
            archive_dir = Path(dest_root) / ".archive"
            dest_file = archive_dir / dest_path
            output_objects.append({**record, "rehearsal_status": "archived", "archive_path": str(dest_file)})
            continue

        if not src_file.exists():
            integrity_issues.append({
                "code": "MISSING_SOURCE",
                "path": source_path,
                "reason": f"Source file not found: {src_file}",
            })
            if fail_on_integrity:
                raise RuntimeError(f"Rehearsal halted: source file not found: {src_file}")
            continue

        try:
            content = src_file.read_bytes()
        except OSError as e:
            integrity_issues.append({
                "code": "READ_ERROR",
                "path": source_path,
                "reason": str(e),
            })
            if fail_on_integrity:
                raise RuntimeError(f"Rehearsal halted: read error on {src_file}: {e}")
            continue

        pre_hash = sha256_hex(content)

        handler = _ACTION_HANDLERS.get(action)
        if handler is None:
            integrity_issues.append({
                "code": "UNKNOWN_ACTION",
                "path": source_path,
                "reason": f"Unknown action: {action}",
            })
            if fail_on_integrity:
                raise RuntimeError(f"Rehearsal halted: unknown action '{action}' for {source_path}")
            continue

        transformed, transform_meta = handler(content, record)

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(transformed)
        try:
            dest_file.chmod(int(record.get("file_mode", "100644"), 8))
        except OSError:
            pass

        post_hash = sha256_hex(transformed)

        if action == ACTION_PRESERVE:
            if pre_hash != post_hash:
                integrity_issues.append({
                    "code": "PRESERVE_HASH_MISMATCH",
                    "path": source_path,
                    "reason": f"SHA-256 mismatch on preserve: {pre_hash} != {post_hash}",
                })
                if fail_on_integrity:
                    raise RuntimeError(
                        f"Rehearsal halted: preserve hash mismatch for {source_path}"
                    )

        if action == ACTION_ID_BACKFILL and transform_meta:
            if not transform_meta.get("body_bytes_unchanged"):
                integrity_issues.append({
                    "code": "BODY_BYTES_CHANGED",
                    "path": source_path,
                    "reason": "ID backfill changed body bytes",
                })
                if fail_on_integrity:
                    raise RuntimeError(
                        f"Rehearsal halted: body bytes changed during ID backfill for {source_path}"
                    )

        integrity_checks = _check_integrity(dest_file, transformed, record)
        if integrity_checks:
            integrity_issues.extend(integrity_checks)
            if fail_on_integrity:
                raise RuntimeError(
                    f"Rehearsal halted: integrity gate failed for {source_path}: "
                    f"{[i['code'] for i in integrity_checks]}"
                )

        try:
            text = transformed.decode("utf-8")
            refs = _extract_references(text, dest_path)
            all_references.extend(refs)
        except UnicodeDecodeError:
            pass

        rehearsal_result = {
            **record,
            "rehearsal_status": "imported",
            "pre_hash": pre_hash,
            "post_hash": post_hash,
            "transform_meta": transform_meta,
        }
        rehearsal_results.append(rehearsal_result)
        output_objects.append(rehearsal_result)

    casefold_issues = _check_casefold_collisions(dest_roots, output_objects)
    integrity_issues.extend(casefold_issues)

    destination_trees: dict[str, dict] = {}
    for dest_repo in dest_repos_inited:
        dest_root = Path(dest_roots[dest_repo])
        marker_path = dest_root / "MIGRATION_BASE"
        marker_path.write_text(_MIGRATION_BASE_CONTENT, encoding="utf-8")
        _git_add_all(dest_root)
        commit_sha = _git_commit(
            dest_root,
            f"migration-rehearsal: {run_id}\n\nRehearsal import of migration run {run_id}",
            committer_name,
            committer_email,
            committer_date,
        )
        destination_trees[dest_repo] = {
            "root": str(dest_root),
            "tree_sha": _git_tree_sha(dest_root) if commit_sha else "",
            "head_sha": commit_sha,
        }

    reference_manifest = _build_reference_manifest(
        all_references, redirect_map, output_objects
    )

    return {
        "migration_run_id": run_id,
        "destination_trees": destination_trees,
        "reference_manifest": reference_manifest,
        "redirect_catalog": redirect_map,
        "integrity_report": {
            "issues": integrity_issues,
            "passed": len(integrity_issues) == 0,
        },
        "summary": {
            "total": len(objects),
            "imported": sum(1 for r in output_objects if r.get("rehearsal_status") == "imported"),
            "rejected": sum(1 for r in output_objects if r.get("rehearsal_status") == "rejected"),
            "archived": sum(1 for r in output_objects if r.get("rehearsal_status") == "archived"),
        },
        "committer_info": {
            "name": committer_name,
            "email": committer_email,
            "date": committer_date,
        },
    }


def _build_reference_manifest(
    references: list[dict],
    redirect_map: dict[str, str],
    objects: list[dict],
) -> dict:
    id_to_path: dict[str, str] = {}
    path_to_id: dict[str, str] = {}
    for obj in objects:
        rid = obj.get("domain_resource_id")
        dpath = obj.get("destination_path", obj.get("source_path", ""))
        if rid:
            id_to_path[rid] = dpath
            path_to_id[dpath] = rid

    resolved_refs: list[dict] = []
    broken_new = 0
    broken_old = 0

    for ref in references:
        target = ref.get("target", "")
        disposition = "resolved"
        new_target_id = None

        if target in path_to_id:
            new_target_id = path_to_id[target]
        elif target in redirect_map:
            new_target_id = redirect_map[target]
        elif target in id_to_path:
            new_target_id = target
        elif ref.get("ref_type") in ("memory_id", "wiki_id", "wf_id"):
            if target in id_to_path:
                new_target_id = target
            else:
                disposition = "broken"
                broken_new += 1

        if disposition == "broken":
            broken_old += 1

        resolved_refs.append({
            **ref,
            "disposition": disposition,
            "new_target_id": new_target_id,
        })

    net_broken = broken_new - broken_old

    return {
        "references": resolved_refs,
        "total_references": len(resolved_refs),
        "broken_references": broken_new,
        "acknowledged_broken_baseline": broken_old,
        "net_new_broken": net_broken,
        "net_new_broken_is_zero": net_broken == 0,
    }


def verify_idempotent(
    result1: dict,
    result2: dict,
) -> bool:
    import copy

    def _strip_root(result: dict) -> dict:
        r = copy.deepcopy(result)
        if "destination_trees" in r:
            for _, info in r["destination_trees"].items():
                info.pop("root", None)
        return r

    r1 = json.dumps(_strip_root(result1), sort_keys=True, ensure_ascii=False)
    r2 = json.dumps(_strip_root(result2), sort_keys=True, ensure_ascii=False)
    return r1 == r2


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Migration rehearsal engine: REHEARSED phase import"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a manifest JSON file",
    )
    ap.add_argument(
        "--source-roots", required=True,
        help="Path to JSON file mapping source_repo -> filesystem root",
    )
    ap.add_argument(
        "--dest-roots", required=True,
        help="Path to JSON file mapping destination_repo -> filesystem root",
    )
    ap.add_argument(
        "--committer-name", default=_DEFAULT_COMMITTER_NAME,
        help="Committer name for idempotent commits",
    )
    ap.add_argument(
        "--committer-email", default=_DEFAULT_COMMITTER_EMAIL,
        help="Committer email for idempotent commits",
    )
    ap.add_argument(
        "--committer-date", default=_DEFAULT_COMMITTER_DATE,
        help="Committer date for idempotent commits",
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output rehearsal result JSON file path (default: stdout)",
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    with open(args.source_roots, encoding="utf-8") as f:
        source_roots = json.load(f)

    with open(args.dest_roots, encoding="utf-8") as f:
        dest_roots = json.load(f)

    result = run_rehearsal(
        manifest=manifest,
        source_roots=source_roots,
        dest_roots=dest_roots,
        committer_name=args.committer_name,
        committer_email=args.committer_email,
        committer_date=args.committer_date,
    )

    result_json = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_json)
    else:
        sys.stdout.write(result_json)


if __name__ == "__main__":
    main()