"""Migration inventory: deterministic read-only scanner + manifest generator.

M3a INVENTORIED-phase tool.  Scans source sets per design §8.1,
produces a deterministic, immutable migration manifest (§8.4 fields),
and verifies the global invariant tracked == preserved + transformed +
archived + rejected with unclassified == 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

# ── Constants ─────────────────────────────────────────────────────────────────

MEMORY_ID_RE = re.compile(r"^m-[0-9a-f]{6}$")
WIKI_ID_RE = re.compile(r"^w-[0-9a-f]{6}$")
WF_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")

DEFAULT_RUN_ID_PREFIX = "mig-"

# Exception codes (§8.2 risk items)
EXC_YAML_PARSE = "YAML_PARSE_ERROR"
EXC_MISSING_BRIEF = "MISSING_BRIEF"
EXC_DUPLICATE_BASENAME = "DUPLICATE_BASENAME"
EXC_DUPLICATE_ID = "DUPLICATE_ID"
EXC_CASEFOLD_COLLISION = "CASEFOLD_COLLISION"
EXC_PATH_LENGTH = "PATH_LENGTH_EXCEEDED"
EXC_SYMLINK = "SYMLINK"
EXC_LFS_POINTER = "LFS_POINTER"
EXC_CREDENTIAL_SYMLINK = "CREDENTIAL_SYMLINK"
EXC_EXECUTABLE = "EXECUTABLE_BIT"
EXC_BINARY = "BINARY_BYTES"
EXC_READ_ERROR = "READ_ERROR"

# Actions
ACTION_PRESERVE = "preserve"
ACTION_ID_BACKFILL = "id_backfill"
ACTION_NORMALIZE = "normalize"
ACTION_REWRITE = "rewrite"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTION_REJECT = "reject"

_TRANSFORM_ACTIONS = {ACTION_ID_BACKFILL, ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE}

# Path length limits
MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

# Wiki schema-scope basenames that are expected to be enumerated
_WIKI_SCHEMA_BASENAMES = {"WIKI.md", "log.md"}

# Credential-related path patterns
_CREDENTIAL_PATTERNS = [
    re.compile(r"(^|/)(\.env|credentials?|secrets?|tokens?|\.netrc|\.git-credentials)(\..*)?$", re.IGNORECASE),
    re.compile(r"(^|/)\.ssh/"),
    re.compile(r"(^|/)\.gnupg/"),
]


def _is_credential_path(path: str) -> bool:
    return any(p.search(path) for p in _CREDENTIAL_PATTERNS)


# ── ID generation ─────────────────────────────────────────────────────────────

def deterministic_id(content: bytes, prefix: str) -> str:
    h = hashlib.sha256(content).hexdigest()
    return prefix + h[:6]


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_blob_oid(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def _generate_run_id(source_sets: list[dict]) -> str:
    parts = sorted(
        f"{ss['source_repo']}@{ss.get('source_commit', '0000000000000000000000000000000000000000')}"
        for ss in source_sets
    )
    h = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return DEFAULT_RUN_ID_PREFIX + h[:12]


# ── File scanning ─────────────────────────────────────────────────────────────

def is_binary(content: bytes) -> bool:
    return b"\x00" in content[:8192]


def is_lfs_pointer(content: bytes) -> bool:
    return content.startswith(b"version https://git-lfs.github.com/spec/v1")


def _extract_lfs_oid(content: bytes) -> str | None:
    if not is_lfs_pointer(content):
        return None
    content_str = content.decode("utf-8", errors="replace")
    for line in content_str.splitlines():
        if line.startswith("oid sha256:"):
            return line[len("oid sha256:"):].strip()
    return None


def _parse_memory_frontmatter(content: bytes) -> dict | None:
    if not content.startswith(b"---\n"):
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return None
    fm_text = text[4:4 + fm_end.start() + 1]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm


def _classify_wiki_file(rel_path: str) -> str | None:
    """Classify a wiki file into writable/raw/schema based on path."""
    parts = Path(rel_path).parts

    if parts and parts[0] == "Zettelkasten":
        return "wiki_writable"
    if parts and parts[0] == "转换文档":
        return "wiki_raw"
    if len(parts) >= 2 and parts[0] == "DeepThought":
        return "wiki_raw"
    if str(rel_path) == "findings.md":
        return "wiki_raw"
    # Check for asset closure: files referenced by findings.md or report.md
    if str(rel_path).startswith("findings_assets/") or str(rel_path).startswith("DeepThought/") and "/assets/" in str(rel_path):
        return "wiki_raw"
    if Path(rel_path).name in _WIKI_SCHEMA_BASENAMES:
        return "wiki_schema"
    if parts and parts[0] == "inbox":
        return "wiki_schema"
    return None


def scan_file(
    path: Path,
    root: Path,
    source_repo: str,
    source_commit: str,
    object_class: str,
    prefix: str,
    destination_repo: str,
    default_action: str,
    migration_run_id: str,
) -> dict:
    rel_path = str(path.relative_to(root))

    if path.is_symlink():
        exception_code = EXC_CREDENTIAL_SYMLINK if _is_credential_path(rel_path) else EXC_SYMLINK
        reason = "Credential symlink rejected" if exception_code == EXC_CREDENTIAL_SYMLINK else "Symlinks are rejected by default (no dereference)"
        return {
            "migration_run_id": migration_run_id,
            "source_repo": source_repo,
            "source_commit": source_commit,
            "source_path": rel_path,
            "git_blob_oid": None,
            "sha256": None,
            "size": 0,
            "file_mode": "120000",
            "lfs_oid": None,
            "object_class": object_class,
            "destination_repo": destination_repo,
            "destination_path": rel_path,
            "domain_resource_id": None,
            "vfs_node_id": None,
            "action": ACTION_REJECT,
            "pre_hash": None,
            "post_hash": None,
            "allowed_transformations": [],
            "reference_rewrites": [],
            "exception_code": exception_code,
            "reason": reason,
        }

    try:
        content = path.read_bytes()
    except OSError as e:
        return {
            "migration_run_id": migration_run_id,
            "source_repo": source_repo,
            "source_commit": source_commit,
            "source_path": rel_path,
            "git_blob_oid": None,
            "sha256": None,
            "size": 0,
            "file_mode": "000000",
            "lfs_oid": None,
            "object_class": object_class,
            "destination_repo": destination_repo,
            "destination_path": rel_path,
            "domain_resource_id": None,
            "vfs_node_id": None,
            "action": ACTION_REJECT,
            "pre_hash": None,
            "post_hash": None,
            "allowed_transformations": [],
            "reference_rewrites": [],
            "exception_code": EXC_READ_ERROR,
            "reason": f"Failed to read file: {e}",
        }

    exceptions: list[tuple[str, str]] = []

    basename = Path(rel_path).name
    if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
        exceptions.append((EXC_PATH_LENGTH, f"Basename exceeds {MAX_BASENAME_LENGTH} bytes"))
    if len(rel_path.encode("utf-8")) > MAX_PATH_LENGTH:
        exceptions.append((EXC_PATH_LENGTH, f"Path exceeds {MAX_PATH_LENGTH} bytes"))

    if is_binary(content):
        exceptions.append((EXC_BINARY, "File contains binary bytes"))

    if is_lfs_pointer(content):
        exceptions.append((EXC_LFS_POINTER, "File is a git LFS pointer"))

    st = path.stat()
    if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        exceptions.append((EXC_EXECUTABLE, "File has executable bit set"))

    file_sha256 = sha256_hex(content)
    blob_oid = git_blob_oid(content)
    file_size = len(content)
    file_mode = oct(st.st_mode)[-6:]
    lfs_oid = _extract_lfs_oid(content)

    resource_id = deterministic_id(content, prefix)

    existing_id = None
    if content.startswith(b"---\n"):
        fm = _parse_memory_frontmatter(content)
        if fm is not None and fm.get("id"):
            existing_id = str(fm["id"])
            if object_class in ("memory_canonical", "memory_legacy") and MEMORY_ID_RE.match(existing_id):
                resource_id = existing_id
            elif object_class in ("memory_canonical", "memory_legacy"):
                exceptions.append((EXC_MISSING_BRIEF, f"Invalid existing ID format: {existing_id}"))
            if not fm.get("name") or not fm.get("description"):
                exceptions.append((EXC_MISSING_BRIEF, "Missing required fields (name/description) in frontmatter"))
        elif fm is None:
            exceptions.append((EXC_YAML_PARSE, "Failed to parse YAML frontmatter"))
        elif fm is not None and not fm.get("id"):
            if object_class in ("memory_canonical", "memory_legacy"):
                if not fm.get("name") or not fm.get("description"):
                    exceptions.append((EXC_MISSING_BRIEF, "Missing required fields (name/description) in frontmatter"))

    if exceptions:
        action = ACTION_REJECT
    elif existing_id and object_class == "memory_canonical":
        action = ACTION_PRESERVE
    elif object_class == "memory_legacy" and not existing_id:
        action = ACTION_ID_BACKFILL
    else:
        action = default_action

    return {
        "migration_run_id": migration_run_id,
        "source_repo": source_repo,
        "source_commit": source_commit,
        "source_path": rel_path,
        "git_blob_oid": blob_oid,
        "sha256": file_sha256,
        "size": file_size,
        "file_mode": file_mode,
        "lfs_oid": lfs_oid,
        "object_class": object_class,
        "destination_repo": destination_repo,
        "destination_path": rel_path,
        "domain_resource_id": resource_id,
        "vfs_node_id": resource_id,
        "action": action,
        "pre_hash": file_sha256,
        "post_hash": file_sha256,
        "allowed_transformations": [],
        "reference_rewrites": [],
        "exception_code": exceptions[0][0] if exceptions else None,
        "reason": exceptions[0][1] if exceptions else None,
    }


# ── Source-set traversal ──────────────────────────────────────────────────────

def _scan_source_set(
    ss: dict,
    migration_run_id: str,
) -> list[dict]:
    root = Path(ss["root"])
    records: list[dict] = []
    seen_basenames: set[str] = set()
    seen_ids: set[str] = set()
    casefold_map: dict[str, str] = {}

    for pattern in ss.get("include", ["**/*"]):
        if os.path.isabs(pattern):
            resolved = Path(pattern)
            if resolved.exists():
                paths = [resolved]
            else:
                paths = []
        else:
            paths = sorted(root.glob(pattern))

        for p in paths:
            if not p.is_file() and not p.is_symlink():
                continue
            if p.name.startswith(".") and not ss.get("include_dotfiles", False):
                continue

            rel_path = str(p.relative_to(root))

            if ss.get("object_class") == "wiki" and ss.get("auto_classify", True):
                object_class = _classify_wiki_file(rel_path)
                if object_class is None:
                    object_class = "wiki_unknown"
                    default_action = ACTION_PRESERVE
                else:
                    default_action = ACTION_PRESERVE
            else:
                object_class = ss.get("object_class", "unknown")
                default_action = ss.get("default_action", ACTION_PRESERVE)

            record = scan_file(
                path=p,
                root=root,
                source_repo=ss["source_repo"],
                source_commit=ss.get("source_commit", "0000000000000000000000000000000000000000"),
                object_class=object_class,
                prefix=ss["prefix"],
                destination_repo=ss.get("destination_repo", ss["source_repo"]),
                default_action=default_action,
                migration_run_id=migration_run_id,
            )

            basename = Path(record["source_path"]).name
            if basename in seen_basenames:
                if record["exception_code"] is None:
                    record["exception_code"] = EXC_DUPLICATE_BASENAME
                    record["reason"] = f"Duplicate basename: {basename}"
                    record["action"] = ACTION_REJECT
            seen_basenames.add(basename)

            casefold_name = basename.casefold()
            if casefold_name in casefold_map and casefold_map[casefold_name] != basename:
                if record["exception_code"] is None:
                    record["exception_code"] = EXC_CASEFOLD_COLLISION
                    record["reason"] = f"Casefold collision with {casefold_map[casefold_name]}: {basename}"
                    record["action"] = ACTION_REJECT
            else:
                casefold_map[casefold_name] = basename

            if record["domain_resource_id"] and record["domain_resource_id"] in seen_ids:
                if record["exception_code"] is None:
                    record["exception_code"] = EXC_DUPLICATE_ID
                    record["reason"] = f"Duplicate ID: {record['domain_resource_id']}"
                    record["action"] = ACTION_REJECT
            if record["domain_resource_id"]:
                seen_ids.add(record["domain_resource_id"])

            records.append(record)

    return records


# ── Manifest assembly ─────────────────────────────────────────────────────────

def compute_summary(records: list[dict]) -> dict:
    tracked = len(records)
    preserved = sum(1 for r in records if r["action"] == ACTION_PRESERVE)
    transformed = sum(1 for r in records if r["action"] in _TRANSFORM_ACTIONS)
    archived = sum(1 for r in records if r["action"] == ACTION_ARCHIVE)
    rejected = sum(1 for r in records if r["action"] == ACTION_REJECT)
    unclassified = tracked - (preserved + transformed + archived + rejected)

    return {
        "tracked": tracked,
        "preserved": preserved,
        "transformed": transformed,
        "archived": archived,
        "rejected": rejected,
        "unclassified": unclassified,
        "invariant_holds": unclassified == 0,
    }


def build_manifest(
    source_sets: list[dict],
    migration_run_id: str | None = None,
) -> dict:
    if migration_run_id is None:
        migration_run_id = _generate_run_id(source_sets)

    all_records: list[dict] = []
    for ss in source_sets:
        all_records.extend(_scan_source_set(ss, migration_run_id))

    all_records.sort(key=lambda r: (r["source_repo"], r["source_path"]))

    summary = compute_summary(all_records)

    return {
        "migration_run_id": migration_run_id,
        "source_sets": [
            {
                "name": ss.get("name", "unnamed"),
                "source_repo": ss["source_repo"],
                "source_commit": ss.get("source_commit", "0000000000000000000000000000000000000000"),
                "object_class": ss.get("object_class", "unknown"),
                "root": ss["root"],
            }
            for ss in source_sets
        ],
        "objects": all_records,
        "summary": summary,
    }


def run_inventory(
    source_sets: list[dict],
    migration_run_id: str | None = None,
) -> dict:
    return build_manifest(source_sets, migration_run_id)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Migration inventory: deterministic read-only scan + manifest generator"
    )
    ap.add_argument(
        "--source-sets", required=True,
        help="Path to JSON file defining source sets"
    )
    ap.add_argument(
        "--run-id", default=None,
        help="Migration run ID (auto-generated if not provided)"
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output manifest JSON file path (default: stdout)"
    )
    args = ap.parse_args()

    with open(args.source_sets, encoding="utf-8") as f:
        source_sets = json.load(f)

    manifest = build_manifest(source_sets, args.run_id)

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(manifest_json)
    else:
        sys.stdout.write(manifest_json)


if __name__ == "__main__":
    main()