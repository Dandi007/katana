"""Migration inventory: deterministic read-only scanner + manifest generator.

M3a INVENTORIED-phase tool.  Scans source sets per design §8.1,
produces a deterministic, immutable migration manifest (§8.4 fields),
and verifies the global invariant tracked == preserved + transformed +
archived + rejected with unclassified == 0.

ID generation uses a source identity hash (SHA-256 prefix) rather than the
kernel ResourceIdLedger's random gen_id.  The prefix/length alignment
(m-/w-/wf- + 6 hex) matches the kernel convention.  IDs are allocated in a
stable source-identity order so the short namespace remains collision-free
within a manifest, including IDs reserved by rejected objects and a ledger.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

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
EXC_DESTINATION_PATH_CONFLICT = "DESTINATION_PATH_CONFLICT"
EXC_CASEFOLD_COLLISION = "CASEFOLD_COLLISION"
EXC_PATH_LENGTH = "PATH_LENGTH_EXCEEDED"
EXC_SYMLINK = "SYMLINK"
EXC_LFS_POINTER = "LFS_POINTER"
EXC_CREDENTIAL_SYMLINK = "CREDENTIAL_SYMLINK"
EXC_EXECUTABLE = "EXECUTABLE_BIT"
EXC_BINARY = "BINARY_BYTES"
EXC_UNICODE_NFC = "UNICODE_NFC"
EXC_READ_ERROR = "READ_ERROR"

# Actions
ACTION_PRESERVE = "preserve"
ACTION_ID_BACKFILL = "id_backfill"
ACTION_NORMALIZE = "normalize"
ACTION_REWRITE = "rewrite"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTION_QUARANTINE = "quarantine"
ACTION_REJECT = "reject"

_TRANSFORM_ACTIONS = {
    ACTION_ID_BACKFILL,
    ACTION_NORMALIZE,
    ACTION_REWRITE,
    ACTION_MERGE,
    ACTION_QUARANTINE,
}

NORMALIZE_EXECUTABLE = "clear_executable_bit"
NORMALIZE_UNICODE_NFC = "unicode_nfc"

# Path length limits
MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

# Wiki schema-scope basenames that are expected to be enumerated
_WIKI_SCHEMA_BASENAMES = {"WIKI.md", "log.md"}

# These classes retain their source hierarchy at the destination.  Their
# identity and collision keys therefore include the complete relative path.
_PATH_PRESERVING_CLASSES = {"work_folder", "wiki_raw"}

# Credential-related path patterns
_CREDENTIAL_PATTERNS = [
    re.compile(r"(^|/)(\.env|credentials?|secrets?|tokens?|\.netrc|\.git-credentials)(\..*)?$", re.IGNORECASE),
    re.compile(r"(^|/)\.ssh/"),
    re.compile(r"(^|/)\.gnupg/"),
]


def _is_credential_path(path: str) -> bool:
    return any(p.search(path) for p in _CREDENTIAL_PATTERNS)


# ── ID generation ─────────────────────────────────────────────────────────────

def deterministic_id(
    content: bytes,
    prefix: str,
    ledger: object | None = None,
    reserved: set[str] | None = None,
) -> str:
    counter = 0
    while True:
        candidate_content = content if counter == 0 else content + counter.to_bytes(4, "big")
        candidate = prefix + hashlib.sha256(candidate_content).hexdigest()[:6]
        tombstoned = (
            ledger is not None
            and hasattr(ledger, "is_tombstoned")
            and ledger.is_tombstoned(candidate)
        )
        if not tombstoned and (reserved is None or candidate not in reserved):
            return candidate
        counter += 1


def _source_identity(source_repo: str, object_class: str, source_path: str) -> bytes:
    return f"{source_repo}\0{object_class}\0{source_path}".encode("utf-8")


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


def is_nfc_normalized(content: bytes) -> bool:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return unicodedata.is_normalized("NFC", text)


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
    if str(rel_path).startswith("findings_assets/") or (str(rel_path).startswith("DeepThought/") and "/assets/" in str(rel_path)):
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
    ledger: object | None = None,
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
            "normalizations": [],
            "preservation_modes": [],
            "brief_backfill_needed": False,
            "quarantine_path": None,
            "disposition": ACTION_REJECT,
            "exception_codes": [exception_code],
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
            "normalizations": [],
            "preservation_modes": [],
            "brief_backfill_needed": False,
            "quarantine_path": None,
            "disposition": ACTION_REJECT,
            "exception_codes": [EXC_READ_ERROR],
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

    if not is_nfc_normalized(content):
        exceptions.append((EXC_UNICODE_NFC, "File content is not NFC normalized"))

    st = path.stat()
    if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        exceptions.append((EXC_EXECUTABLE, "File has executable bit set"))

    file_sha256 = sha256_hex(content)
    blob_oid = git_blob_oid(content)
    file_size = len(content)
    file_mode = oct(st.st_mode)[-6:]
    lfs_oid = _extract_lfs_oid(content)

    id_identity = _source_identity(source_repo, object_class, rel_path)
    resource_id = deterministic_id(id_identity, prefix, ledger=ledger)
    id_kind = "derived"

    existing_id = None
    if content.startswith(b"---\n"):
        fm = _parse_memory_frontmatter(content)
        if fm is not None and fm.get("id"):
            existing_id = str(fm["id"])
            if object_class in ("memory_canonical", "memory_legacy") and MEMORY_ID_RE.match(existing_id):
                resource_id = existing_id
                id_kind = "existing"
            elif object_class in ("memory_canonical", "memory_legacy"):
                exceptions.append((EXC_MISSING_BRIEF, f"Invalid existing ID format: {existing_id}"))
            elif object_class.startswith("wiki_") and WIKI_ID_RE.match(existing_id):
                resource_id = existing_id
                id_kind = "existing"
            if object_class in ("memory_canonical", "memory_legacy") and (
                not fm.get("name") or not fm.get("description")
            ):
                exceptions.append((EXC_MISSING_BRIEF, "Missing required fields (name/description) in frontmatter"))
        elif fm is None:
            exceptions.append((EXC_YAML_PARSE, "Failed to parse YAML frontmatter"))
        elif fm is not None and not fm.get("id"):
            if object_class in ("memory_canonical", "memory_legacy"):
                if not fm.get("name") or not fm.get("description"):
                    exceptions.append((EXC_MISSING_BRIEF, "Missing required fields (name/description) in frontmatter"))

    if existing_id and object_class == "memory_canonical":
        action = ACTION_PRESERVE
    elif object_class == "memory_legacy" and not existing_id:
        action = ACTION_ID_BACKFILL
    else:
        action = default_action

    exception_codes = [code for code, _ in exceptions]
    normalizations = []
    preservation_modes = []
    if EXC_EXECUTABLE in exception_codes:
        normalizations.append(NORMALIZE_EXECUTABLE)
    if EXC_UNICODE_NFC in exception_codes:
        normalizations.append(NORMALIZE_UNICODE_NFC)
    if EXC_BINARY in exception_codes:
        preservation_modes.append("binary_bytes")
    if EXC_LFS_POINTER in exception_codes:
        preservation_modes.append("lfs_pointer")

    # Content anomalies have explicit, non-destructive dispositions.  Structural
    # conflicts and invalid memory metadata remain blocking.
    blocking_codes = {EXC_PATH_LENGTH, EXC_MISSING_BRIEF}
    if any(code in blocking_codes for code in exception_codes):
        action = ACTION_REJECT
    elif EXC_YAML_PARSE in exception_codes:
        action = ACTION_QUARANTINE
        normalizations = [
            item for item in normalizations if item == NORMALIZE_EXECUTABLE
        ]
    elif EXC_BINARY in exception_codes or EXC_LFS_POINTER in exception_codes:
        action = ACTION_NORMALIZE if normalizations else ACTION_PRESERVE
    elif normalizations and action == ACTION_PRESERVE:
        action = ACTION_NORMALIZE

    disposition = action
    quarantine_path = f"_quarantine/{rel_path}" if action == ACTION_QUARANTINE else None
    post_hash = file_sha256
    if action == ACTION_NORMALIZE and NORMALIZE_UNICODE_NFC in normalizations:
        normalized = unicodedata.normalize("NFC", content.decode("utf-8")).encode("utf-8")
        post_hash = sha256_hex(normalized)

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
        "post_hash": post_hash,
        "allowed_transformations": list(normalizations),
        "normalizations": normalizations,
        "preservation_modes": preservation_modes,
        "brief_backfill_needed": False,
        "quarantine_path": quarantine_path,
        "disposition": disposition,
        "exception_codes": exception_codes,
        "reference_rewrites": [],
        "exception_code": exceptions[0][0] if exceptions else None,
        "reason": exceptions[0][1] if exceptions else None,
        "_id_identity": id_identity.decode("utf-8"),
        "_id_kind": id_kind,
        "_id_prefix": prefix,
        "_source_root": str(root),
    }


# ── Source-set traversal ──────────────────────────────────────────────────────

def _is_path_preserving(object_class: str) -> bool:
    return object_class in _PATH_PRESERVING_CLASSES


def _set_exception(record: dict, code: str, reason: str) -> None:
    codes = record.setdefault("exception_codes", [])
    if code not in codes:
        codes.append(code)
    if record["exception_code"] is None or record["action"] != ACTION_REJECT:
        record["exception_code"] = code
        record["reason"] = reason
    record["action"] = ACTION_REJECT
    record["disposition"] = ACTION_REJECT
    record["quarantine_path"] = None


def _date_topic_root(rel_path: Path) -> Path | None:
    parts = rel_path.parts
    if len(parts) < 5 or not re.fullmatch(r"\d{4}", parts[0]):
        return None
    try:
        datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None
    return Path(*parts[:4])


def _work_folder_roots(
    root: Path,
    paths: list[Path],
    root_depth: int | None = None,
) -> list[Path]:
    """Return structural topic roots, never marker-bearing nested directories."""
    roots: set[Path] = set()
    for path in paths:
        rel_path = path.relative_to(root)
        if root_depth is None:
            folder = _date_topic_root(rel_path)
        elif root_depth == 0:
            folder = Path(".")
        elif len(rel_path.parts) > root_depth:
            folder = Path(*rel_path.parts[:root_depth])
        else:
            folder = None
        if folder is not None:
            roots.add(folder)
    return sorted(roots, key=lambda p: (-len(p.parts), p.as_posix()))


def _containing_work_folder(rel_path: Path, folder_roots: list[Path]) -> Path | None:
    for folder in folder_roots:
        try:
            rel_path.relative_to(folder)
        except ValueError:
            continue
        return folder
    return None


def _work_folder_id(
    root: Path,
    folder: Path,
    source_repo: str,
    prefix: str,
    ledger: object | None,
) -> tuple[str, str, bytes]:
    identity = _source_identity(source_repo, "work_folder", folder.as_posix())
    brief = root / folder / "_brief.md"
    if brief.is_file() and not brief.is_symlink():
        try:
            fm = _parse_memory_frontmatter(brief.read_bytes())
        except OSError:
            fm = None
        if fm is not None:
            existing_id = str(fm.get("id", ""))
            if WF_ID_RE.match(existing_id):
                return existing_id, "existing", identity

    return deterministic_id(identity, prefix, ledger=ledger), "derived", identity


def _scan_source_set(
    ss: dict,
    migration_run_id: str,
    ledger: object | None = None,
) -> tuple[list[dict], dict[str, str]]:
    root = Path(ss["root"])
    records: list[dict] = []
    seen_basenames: set[str] = set()
    redirect_map: dict[str, str] = {}

    matched_paths: dict[str, Path] = {}
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
            matched_paths[str(p)] = p

    paths = sorted(matched_paths.values(), key=lambda p: str(p.relative_to(root)))
    folder_roots = (
        _work_folder_roots(root, paths, ss.get("work_folder_root_depth"))
        if ss.get("object_class") == "work_folder"
        else []
    )
    destination_repo = ss.get("destination_repo", ss["source_repo"])
    folder_ids = {
        folder: _work_folder_id(root, folder, ss["source_repo"], ss["prefix"], ledger)
        for folder in folder_roots
    }

    for p in paths:
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
            destination_repo=destination_repo,
            default_action=default_action,
            migration_run_id=migration_run_id,
            ledger=ledger,
        )

        work_folder = None
        if object_class == "work_folder":
            work_folder = _containing_work_folder(Path(rel_path), folder_roots)
            record["work_folder_path"] = work_folder.as_posix() if work_folder is not None else None
            if work_folder is not None:
                resource_id, id_kind, id_identity = folder_ids[work_folder]
                record["domain_resource_id"] = resource_id
                record["vfs_node_id"] = resource_id
                record["_id_kind"] = id_kind
                record["_id_identity"] = id_identity.decode("utf-8")
            if work_folder is not None and record["action"] == ACTION_PRESERVE:
                record["disposition"] = "preserve-active"

        basename = Path(record["source_path"]).name
        if not _is_path_preserving(object_class):
            if basename in seen_basenames:
                _set_exception(record, EXC_DUPLICATE_BASENAME, f"Duplicate basename: {basename}")
            seen_basenames.add(basename)

        if record["action"] == ACTION_ID_BACKFILL and record["domain_resource_id"]:
            redirect_map[record["source_path"]] = record["domain_resource_id"]

        records.append(record)

    if folder_roots:
        for folder in folder_roots:
            brief = root / folder / "_brief.md"
            if brief.is_file() and not brief.is_symlink():
                continue
            folder_path = folder.as_posix()
            for record in records:
                if record.get("work_folder_path") == folder_path:
                    record["brief_backfill_needed"] = True
                    codes = record.setdefault("exception_codes", [])
                    if EXC_MISSING_BRIEF not in codes:
                        codes.append(EXC_MISSING_BRIEF)
                    if record["action"] != ACTION_REJECT:
                        if record["exception_code"] is None:
                            record["exception_code"] = EXC_MISSING_BRIEF
                            record["reason"] = f"Work folder missing _brief.md: {folder_path}"
                        if record["action"] == ACTION_PRESERVE:
                            record["disposition"] = "preserve-active"

    return records, redirect_map


# ── Manifest assembly ─────────────────────────────────────────────────────────

def _casefold_value(record: dict, *, source: bool = False) -> str:
    path = record["source_path"] if source else record["destination_path"]
    if _is_path_preserving(record["object_class"]):
        return path
    return Path(path).name


def _casefold_key(record: dict, *, source: bool = False) -> str:
    return unicodedata.normalize("NFC", _casefold_value(record, source=source)).casefold()


def _casefold_signature(
    destination_repo: str,
    collision_key: str,
    paths: list[str],
) -> tuple[str, str, tuple[str, ...]]:
    return destination_repo, collision_key, tuple(sorted(paths))


def _allocate_derived_ids(records: list[dict], ledger: object | None) -> None:
    reserved = {
        record["domain_resource_id"]
        for record in records
        if record.get("domain_resource_id") and record.get("_id_kind") == "existing"
    }
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        if not record.get("domain_resource_id") or record.get("_id_kind") != "derived":
            continue
        key = (record["_id_prefix"], record["_id_identity"])
        groups.setdefault(key, []).append(record)

    for (prefix, identity), identity_records in sorted(groups.items()):
        resource_id = deterministic_id(
            identity.encode("utf-8"), prefix, ledger=ledger, reserved=reserved
        )
        reserved.add(resource_id)
        for record in identity_records:
            record["domain_resource_id"] = resource_id
            record["vfs_node_id"] = resource_id


def _mark_global_conflicts(records: list[dict]) -> list[dict]:
    destination_groups: dict[tuple[str, str], list[dict]] = {}
    casefold_groups: dict[tuple[str, str], list[dict]] = {}
    acknowledged_casefold: list[dict] = []

    for record in records:
        destination_repo = record["destination_repo"]
        destination_path = record["destination_path"]
        destination_groups.setdefault((destination_repo, destination_path), []).append(record)
        casefold_groups.setdefault((destination_repo, _casefold_key(record)), []).append(record)

    for (_, destination_path), conflicting in destination_groups.items():
        if len(conflicting) < 2:
            continue
        for record in conflicting:
            _set_exception(
                record,
                EXC_DESTINATION_PATH_CONFLICT,
                f"Multiple sources map to destination path: {destination_path}",
            )

    for (destination_repo, collision_key), conflicting in casefold_groups.items():
        paths = {_casefold_value(record) for record in conflicting}
        if len(paths) < 2:
            continue
        source_origins = {
            (record["source_repo"], record["source_commit"], record.get("_source_root"))
            for record in conflicting
        }
        source_keys = {_casefold_key(record, source=True) for record in conflicting}
        pre_existing = len(source_origins) == 1 and len(source_keys) == 1
        rendered_paths = ", ".join(sorted(paths))
        if pre_existing:
            for record in conflicting:
                record["casefold_collision_status"] = "acknowledged_pre_existing"
            source_repo, source_commit, _ = next(iter(source_origins))
            acknowledged_casefold.append({
                "destination_repo": destination_repo,
                "collision_key": collision_key,
                "destination_paths": sorted(paths),
                "source_repo": source_repo,
                "source_commit": source_commit,
                "source_paths": sorted({record["source_path"] for record in conflicting}),
            })
            continue
        for record in conflicting:
            record["casefold_collision_status"] = "migration_introduced"
            _set_exception(
                record,
                EXC_CASEFOLD_COLLISION,
                f"Destination path casefold collision: {rendered_paths}",
            )

    id_groups: dict[str, dict[tuple[str, str, str], list[dict]]] = {}
    for record in records:
        resource_id = record.get("domain_resource_id")
        if not resource_id:
            continue
        identity_path = record.get("work_folder_path") or record["source_path"]
        source_identity = (
            record["object_class"],
            record["source_repo"],
            identity_path,
        )
        id_groups.setdefault(resource_id, {}).setdefault(source_identity, []).append(record)

    for resource_id, identities in id_groups.items():
        if len(identities) < 2:
            continue
        for identity_records in identities.values():
            for record in identity_records:
                _set_exception(record, EXC_DUPLICATE_ID, f"Duplicate ID: {resource_id}")

    return sorted(
        acknowledged_casefold,
        key=lambda item: (
            item["destination_repo"], item["collision_key"], item["destination_paths"]
        ),
    )

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
    ledger_path: str | None = None,
) -> dict:
    if migration_run_id is None:
        migration_run_id = _generate_run_id(source_sets)

    ledger = None
    if ledger_path is not None:
        try:
            from katana_kernel.ledger import ResourceIdLedger
            ledger = ResourceIdLedger(ledger_path)
        except ImportError:
            pass

    all_records: list[dict] = []
    for ss in source_sets:
        records, _ = _scan_source_set(ss, migration_run_id, ledger=ledger)
        all_records.extend(records)

    all_records.sort(key=lambda r: (r["source_repo"], r["source_path"]))
    _allocate_derived_ids(all_records, ledger)
    acknowledged_casefold = _mark_global_conflicts(all_records)
    all_redirects = {
        record["source_path"]: record["domain_resource_id"]
        for record in all_records
        if record["action"] == ACTION_ID_BACKFILL and record.get("domain_resource_id")
    }
    for record in all_records:
        for internal_key in ("_id_identity", "_id_kind", "_id_prefix", "_source_root"):
            record.pop(internal_key, None)

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
                "destination_repo": ss.get("destination_repo", ss["source_repo"]),
                "work_folder_root_depth": ss.get("work_folder_root_depth"),
            }
            for ss in source_sets
        ],
        "objects": all_records,
        "redirect_map": all_redirects,
        "acknowledged_baseline": {
            "casefold_collisions": acknowledged_casefold,
        },
        "summary": summary,
    }


def run_inventory(
    source_sets: list[dict],
    migration_run_id: str | None = None,
    ledger_path: str | None = None,
) -> dict:
    return build_manifest(source_sets, migration_run_id, ledger_path=ledger_path)


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
