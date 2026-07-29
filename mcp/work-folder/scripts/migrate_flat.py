#!/usr/bin/env python3
"""Fail-closed offline migration from date anchors to flat Work Folder IDs.

The tool is deliberately separate from the live Work Folder MCP.  It operates
on an explicitly named Git repository and legacy root in four phases:

1. ``inventory`` classifies the repository without changing it.
2. ``plan`` produces a deterministic, content-addressed migration map.
3. ``apply`` checks maintenance/CAS gates and moves every topic with ``git mv``.
4. ``verify`` proves the post-move tree and Git diff match the plan.

No phase commits, deletes, cleans, resets, or talks to a running service.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


SCHEMA_VERSION = 2
PLAN_KIND = "katana-work-folder-flat-migration"
MAINTENANCE_MODE = "work-folder-offline-flat-migration"
ID_DOMAIN = b"katana.work-folder.flat-migration.v1\0"

WF_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
YEAR_RE = re.compile(r"^[0-9]{4}$")
MONTH_RE = re.compile(r"^[0-9]{2}$")
DAY_RE = re.compile(r"^[0-9]{2}$")
FRONTMATTER_RE = re.compile(
    rb"\A---(?P<open_newline>\r?\n)(?P<yaml>.*?)"
    rb"(?P<close_newline>\r?\n)---(?P<tail>(?:\r?\n|\Z).*)\Z",
    re.DOTALL,
)
ID_LINE_RE = re.compile(
    rb"(?m)^(?:id|[\"']id[\"'])[ \t]*:[^\r\n]*(?P<newline>\r?\n|\Z)"
)
GOAL_RE = re.compile(r"\*\*Goal:\*\*[ \t]*([^\r\n]*)")

REPO_ROOT_CONTROL_NAMES = {
    "INDEX.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".gitignore",
    ".gitkeep",
    ".katana",
    "MIGRATION_BASE.json",
}
LEGACY_ROOT_CONTROL_NAMES = {
    "INDEX.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".gitignore",
    ".gitkeep",
    ".katana",
}
DOUBLE_ROOT_RELATIVE = PurePosixPath("智元工作/工作记录")
RUNTIME_TOPIC_DIRS = {".review-loop", ".sessions", ".superpowers"}
RESERVED_TOPIC_SEGMENTS = {".git", ".katana"}
FLAT_LAYOUT_PAYLOAD = {"layout": "flat-id-v1", "schema_version": 1}
RUNTIME_GITIGNORE_LINE = "/.katana/runtime/"

REPAIR_STATES = {"missing", "parse_error", "invalid_metadata"}
REPAIR_REQUIRED_FIELDS = ("title", "status", "created", "updated")
VALID_STATUS = {"active", "paused", "archived", "completed"}
MIGRATION_REASONS = {
    "canonical-id",
    "duplicate-canonical-id",
    "legacy-id",
    "missing-id",
    "repair-invalid-metadata",
    "repair-missing-brief",
    "repair-parse-error",
}


class MigrationError(RuntimeError):
    """A stable fail-closed error with a machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def canonical_json(value: Any) -> bytes:
    """Return canonical UTF-8 JSON with one trailing newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _without_key(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in mapping.items() if name != key}


def _hash_without_key(mapping: dict[str, Any], key: str) -> str:
    return sha256_bytes(canonical_json(_without_key(mapping, key)))


def _git(
    repo_root: Path,
    *args: str,
    text: bool = True,
) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    if text:
        return result.stdout.strip()
    return result.stdout


def _git_head(repo_root: Path) -> str:
    return str(_git(repo_root, "rev-parse", "HEAD"))


def _git_tracked_files(repo_root: Path) -> set[str]:
    raw = _git(repo_root, "ls-files", "-z", text=False)
    assert isinstance(raw, bytes)
    return {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }


def _git_ignored_untracked_files(repo_root: Path) -> set[str]:
    raw = _git(
        repo_root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        text=False,
    )
    assert isinstance(raw, bytes)
    return {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }


def _git_untracked_files(repo_root: Path) -> set[str]:
    raw = _git(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        text=False,
    )
    assert isinstance(raw, bytes)
    return {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }


def _nested_git_metadata_paths(repo_root: Path) -> list[str]:
    """Find Git metadata below the one allowed repository-root ``.git``."""

    found: list[str] = []

    def _walk_error(exc: OSError) -> None:
        raise MigrationError(
            "REPOSITORY_SCAN_FAILED",
            f"cannot inspect repository topology: {exc}",
        )

    for current_raw, directory_names, file_names in os.walk(
        repo_root,
        topdown=True,
        followlinks=False,
        onerror=_walk_error,
    ):
        current = Path(current_raw)
        if current == repo_root:
            directory_names[:] = [
                name for name in directory_names if name != ".git"
            ]
            continue
        if ".git" in directory_names:
            found.append((current / ".git").relative_to(repo_root).as_posix())
            directory_names[:] = [
                name for name in directory_names if name != ".git"
            ]
        if ".git" in file_names:
            found.append((current / ".git").relative_to(repo_root).as_posix())
    return sorted(set(found), key=lambda value: value.encode("utf-8"))


def _validate_roots(
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
    *,
    require_legacy: bool = True,
) -> tuple[Path, Path, Path]:
    repo = Path(repo_root).expanduser().resolve()
    legacy = Path(legacy_root).expanduser().resolve()

    if not repo.is_dir():
        raise MigrationError("REPO_ROOT_NOT_FOUND", f"not a directory: {repo}")
    try:
        top = Path(str(_git(repo, "rev-parse", "--show-toplevel"))).resolve()
    except subprocess.CalledProcessError as exc:
        raise MigrationError(
            "NOT_A_GIT_REPOSITORY",
            f"repo root is not a Git working tree: {repo}",
        ) from exc
    if top != repo:
        raise MigrationError(
            "REPO_ROOT_MISMATCH",
            f"explicit repo root {repo} is not Git toplevel {top}",
        )
    if require_legacy and not legacy.is_dir():
        raise MigrationError(
            "LEGACY_ROOT_NOT_FOUND",
            f"not a directory: {legacy}",
        )
    if legacy == repo:
        raise MigrationError(
            "ROOT_OVERLAP",
            "legacy root must be a strict descendant of repo root",
        )
    try:
        relative = legacy.relative_to(repo)
    except ValueError as exc:
        raise MigrationError(
            "ROOT_OVERLAP",
            "legacy root must be inside repo root",
        ) from exc
    if not relative.parts:
        raise MigrationError(
            "ROOT_OVERLAP",
            "legacy root must not equal repo root",
        )
    return repo, legacy, relative


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message}


def _sorted_children(directory: Path) -> list[Path]:
    return sorted(directory.iterdir(), key=lambda item: item.name.encode("utf-8"))


def _control_hashes(
    control: Path,
    repo_root: Path,
    tracked: set[str],
) -> list[dict[str, Any]]:
    if control.name == ".git":
        return []
    if control.is_symlink():
        return [{
            "relative_path": control.relative_to(repo_root).as_posix(),
            "kind": "symlink",
            "target": os.readlink(control),
        }]
    if control.is_file():
        content = control.read_bytes()
        relative = control.relative_to(repo_root).as_posix()
        return [{
            "relative_path": relative,
            "kind": "regular_file",
            "sha256": sha256_bytes(content),
            "size": len(content),
            "git_tracked": relative in tracked,
        }]
    records: list[dict[str, Any]] = []
    for item in sorted(control.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(repo_root).as_posix()
        if item.is_symlink():
            records.append({
                "relative_path": relative,
                "kind": "symlink",
                "target": os.readlink(item),
            })
        elif item.is_file():
            content = item.read_bytes()
            records.append({
                "relative_path": relative,
                "kind": "regular_file",
                "sha256": sha256_bytes(content),
                "size": len(content),
                "git_tracked": relative in tracked,
            })
        elif item.is_dir():
            records.append({"relative_path": relative, "kind": "directory"})
        else:
            records.append({"relative_path": relative, "kind": "special"})
    return records


def _control_record(
    control: Path,
    repo_root: Path,
    classification: str,
    tracked: set[str],
) -> dict[str, Any]:
    return {
        "repo_relative_path": control.relative_to(repo_root).as_posix(),
        "classification": classification,
        "entries": _control_hashes(control, repo_root, tracked),
    }


def _container_record(
    container: Path,
    repo_root: Path,
    classification: str,
) -> dict[str, Any]:
    return {
        "repo_relative_path": container.relative_to(repo_root).as_posix(),
        "classification": classification,
        "entries": [],
    }


def _parse_frontmatter(content: bytes) -> tuple[dict[str, Any], bytes]:
    match = FRONTMATTER_RE.match(content)
    if match is None:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md is missing a complete YAML frontmatter block",
        )
    yaml_bytes = match.group("yaml")
    try:
        yaml_text = yaml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md frontmatter is not UTF-8",
        ) from exc
    try:
        frontmatter = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            f"_brief.md YAML is invalid: {exc}",
        ) from exc
    if not isinstance(frontmatter, dict):
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md frontmatter is not a mapping",
        )
    id_lines = ID_LINE_RE.findall(yaml_bytes)
    if len(id_lines) > 1:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md contains duplicate id keys",
        )
    if "id" in frontmatter and not id_lines:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md id is not a rewrite-safe top-level key",
        )
    return frontmatter, yaml_bytes


def _brief_metadata_problems(
    content: bytes,
    frontmatter: dict[str, Any],
    *,
    require_id: bool,
) -> list[str]:
    match = FRONTMATTER_RE.match(content)
    if match is None:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md is missing a complete YAML frontmatter block",
        )
    try:
        body = match.group("tail").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "_brief.md is not UTF-8",
        ) from exc

    required = REPAIR_REQUIRED_FIELDS
    if require_id:
        required = ("id", *required)
    problems = [
        f"missing explicit metadata: {field}"
        for field in required
        if not frontmatter.get(field)
    ]
    status = frontmatter.get("status")
    if status and (
        not isinstance(status, str)
        or status not in VALID_STATUS
    ):
        problems.append(f"invalid status: {status}")
    goal = GOAL_RE.search(body)
    if goal is None or not goal.group(1).strip():
        problems.append("missing non-empty **Goal:**")
    return problems


def _rewrite_brief_id(content: bytes, new_id: str) -> bytes:
    match = FRONTMATTER_RE.match(content)
    if match is None:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "cannot rewrite id without valid YAML frontmatter",
        )
    yaml_start, yaml_end = match.span("yaml")
    yaml_bytes = content[yaml_start:yaml_end]
    matches = list(ID_LINE_RE.finditer(yaml_bytes))
    if len(matches) > 1:
        raise MigrationError(
            "BRIEF_PARSE_ERROR",
            "cannot rewrite duplicate id keys",
        )
    if matches:
        id_match = matches[0]
        newline = id_match.group("newline")
        replacement = f"id: {new_id}".encode("utf-8") + newline
        rewritten_yaml = (
            yaml_bytes[:id_match.start()]
            + replacement
            + yaml_bytes[id_match.end():]
        )
    else:
        newline = match.group("open_newline")
        rewritten_yaml = (
            f"id: {new_id}".encode("utf-8") + newline + yaml_bytes
        )
    return content[:yaml_start] + rewritten_yaml + content[yaml_end:]


def deterministic_id(
    old_locator: str,
    reserved: set[str],
) -> tuple[str, int]:
    """Allocate a stable domain-separated ID, retrying hash collisions."""

    locator = old_locator.encode("utf-8")
    counter = 0
    while True:
        digest_input = (
            ID_DOMAIN
            + locator
            + b"\0"
            + counter.to_bytes(8, "big")
        )
        candidate = "wf-" + hashlib.sha256(digest_input).hexdigest()[:6]
        if candidate not in reserved:
            return candidate, counter
        counter += 1


def _record_topic(
    repo_root: Path,
    legacy_root: Path,
    topic: Path,
    tracked: set[str],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    locator = topic.relative_to(legacy_root).as_posix()
    files: list[dict[str, Any]] = []
    for item in sorted(topic.rglob("*"), key=lambda value: value.as_posix()):
        item_locator = item.relative_to(repo_root).as_posix()
        if item.is_symlink():
            errors.append(_error(
                "SYMLINK_IN_TOPIC",
                item_locator,
                "topic trees must contain only regular files and directories",
            ))
            continue
        if item.is_dir():
            continue
        if not item.is_file():
            errors.append(_error(
                "SPECIAL_FILE_IN_TOPIC",
                item_locator,
                "topic trees must contain only regular files",
            ))
            continue
        source_relative_path = item.relative_to(topic).as_posix()
        source_parts = PurePosixPath(source_relative_path).parts
        if any(part in RESERVED_TOPIC_SEGMENTS for part in source_parts):
            errors.append(_error(
                "RESERVED_TOPIC_METADATA",
                item_locator,
                "topic payload may not contain .git or .katana segments",
            ))
            continue
        runtime_payload = bool(
            source_parts and source_parts[0] in RUNTIME_TOPIC_DIRS
        )
        if item_locator not in tracked and not runtime_payload:
            errors.append(_error(
                "UNTRACKED_TOPIC_FILE",
                item_locator,
                "untracked topic payload is allowed only in controlled runtime archives",
            ))
        content = item.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            content_kind = "binary"
            api_tool = "fs_read_bytes"
        else:
            content_kind = "utf8_text"
            api_tool = "fs_read"
        if runtime_payload:
            destination_relative_path = PurePosixPath(
                "archive",
                "runtime",
                source_parts[0].lstrip("."),
                *source_parts[1:],
            ).as_posix()
        else:
            destination_relative_path = source_relative_path
        files.append({
            "relative_path": source_relative_path,
            "destination_relative_path": destination_relative_path,
            "repo_relative_path": item_locator,
            "sha256": sha256_bytes(content),
            "size": len(content),
            "git_tracked": item_locator in tracked,
            "content_kind": content_kind,
            "api_tool": api_tool,
        })

    brief = topic / "_brief.md"
    brief_state = "missing"
    brief_error = None
    brief_content_b64 = None
    brief_sha256 = None
    old_id = None
    if brief.exists():
        if brief.is_symlink() or not brief.is_file():
            brief_state = "parse_error"
            brief_error = "_brief.md is not a regular file"
        else:
            brief_content = brief.read_bytes()
            brief_content_b64 = base64.b64encode(brief_content).decode("ascii")
            brief_sha256 = sha256_bytes(brief_content)
            try:
                frontmatter, _ = _parse_frontmatter(brief_content)
                problems = _brief_metadata_problems(
                    brief_content,
                    frontmatter,
                    require_id=False,
                )
                if problems:
                    brief_state = "invalid_metadata"
                    brief_error = "; ".join(problems)
                else:
                    brief_state = "valid"
                if frontmatter.get("id") is not None:
                    old_id = str(frontmatter["id"])
            except MigrationError as exc:
                brief_state = "parse_error"
                brief_error = exc.message

    return {
        "old_locator": locator,
        "source_repo_path": topic.relative_to(repo_root).as_posix(),
        "old_id": old_id,
        "brief_state": brief_state,
        "brief_error": brief_error,
        "brief_sha256": brief_sha256,
        "brief_content_b64": brief_content_b64,
        "is_empty": not files,
        "files": files,
    }


def _validate_container_chain(
    repo_root: Path,
    legacy_root: Path,
    legacy_relative: Path,
    controls: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    current = repo_root
    for index, component in enumerate(legacy_relative.parts):
        expected = current / component
        if index == len(legacy_relative.parts) - 1:
            controls.append(
                _container_record(expected, repo_root, "legacy-root")
            )
            break
        for item in _sorted_children(current):
            if item == expected:
                continue
            if current == repo_root and (
                item.name == ".git"
                or item.name in REPO_ROOT_CONTROL_NAMES
            ):
                continue
            errors.append(_error(
                "UNKNOWN_REPO_ROOT_PAYLOAD",
                item.relative_to(repo_root).as_posix(),
                "payload overlaps the legacy-root container chain",
            ))
        if expected.is_symlink() or not expected.is_dir():
            errors.append(_error(
                "ROOT_OVERLAP",
                expected.relative_to(repo_root).as_posix(),
                "legacy-root container must be a real directory",
            ))
            return
        controls.append(
            _container_record(expected, repo_root, "legacy-container")
        )
        current = expected


def _scan_date_root(
    repo: Path,
    source_root: Path,
    tracked: set[str],
    controls: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    """Scan one physical YYYY/MM/DD/topic root into logical locators."""

    for year_entry in _sorted_children(source_root):
        if year_entry.name in LEGACY_ROOT_CONTROL_NAMES:
            controls.append(
                _control_record(
                    year_entry,
                    repo,
                    "legacy-root-control",
                    tracked,
                )
            )
            continue
        if not YEAR_RE.fullmatch(year_entry.name):
            errors.append(_error(
                "UNKNOWN_LEGACY_ROOT_PAYLOAD",
                year_entry.relative_to(repo).as_posix(),
                "legacy root only permits controls and YYYY directories",
            ))
            continue
        if year_entry.is_symlink() or not year_entry.is_dir():
            errors.append(_error(
                "INVALID_YEAR",
                year_entry.relative_to(repo).as_posix(),
                "YYYY anchor must be a real directory",
            ))
            continue

        for month_entry in _sorted_children(year_entry):
            valid_month = (
                MONTH_RE.fullmatch(month_entry.name)
                and 1 <= int(month_entry.name) <= 12
            )
            if not valid_month:
                errors.append(_error(
                    "INVALID_MONTH",
                    month_entry.relative_to(repo).as_posix(),
                    "month anchor must be MM in 01..12",
                ))
                continue
            if month_entry.is_symlink() or not month_entry.is_dir():
                errors.append(_error(
                    "INVALID_MONTH",
                    month_entry.relative_to(repo).as_posix(),
                    "month anchor must be a real directory",
                ))
                continue

            for day_entry in _sorted_children(month_entry):
                valid_day = (
                    DAY_RE.fullmatch(day_entry.name)
                    and 1 <= int(day_entry.name) <= 31
                )
                if not valid_day:
                    errors.append(_error(
                        "INVALID_DAY",
                        day_entry.relative_to(repo).as_posix(),
                        "day anchor must be DD",
                    ))
                    continue
                try:
                    dt.date(
                        int(year_entry.name),
                        int(month_entry.name),
                        int(day_entry.name),
                    )
                except ValueError:
                    errors.append(_error(
                        "INVALID_DAY",
                        day_entry.relative_to(repo).as_posix(),
                        "day anchor is not a real calendar date",
                    ))
                    continue
                if day_entry.is_symlink() or not day_entry.is_dir():
                    errors.append(_error(
                        "INVALID_DAY",
                        day_entry.relative_to(repo).as_posix(),
                        "day anchor must be a real directory",
                    ))
                    continue

                for topic_entry in _sorted_children(day_entry):
                    if topic_entry.is_symlink() or not topic_entry.is_dir():
                        errors.append(_error(
                            "DAY_LEVEL_PAYLOAD",
                            topic_entry.relative_to(repo).as_posix(),
                            "day anchors may contain topic directories only",
                        ))
                        continue
                    topics.append(_record_topic(
                        repo,
                        source_root,
                        topic_entry,
                        tracked,
                        errors,
                    ))


def _load_tombstones(
    repo: Path,
    candidates: Iterable[Path],
    errors: list[dict[str, str]],
) -> list[str]:
    tombstones: set[str] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        locator = candidate.relative_to(repo).as_posix()
        if candidate.is_symlink() or not candidate.is_file():
            errors.append(_error(
                "INVALID_TOMBSTONE_LEDGER",
                locator,
                "tombstone ledger must be a regular JSON file",
            ))
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(_error(
                "INVALID_TOMBSTONE_LEDGER",
                locator,
                "tombstone ledger is unreadable or invalid JSON",
            ))
            continue
        values = payload.get("tombstones") if isinstance(payload, dict) else None
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not WF_ID_RE.fullmatch(value)
            for value in values
        ):
            errors.append(_error(
                "INVALID_TOMBSTONE_LEDGER",
                locator,
                "tombstones must be canonical wf-* IDs",
            ))
            continue
        tombstones.update(values)
    return sorted(tombstones)


def build_inventory(
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Classify a legacy Work Folder tree without changing it."""

    repo, legacy, legacy_relative = _validate_roots(repo_root, legacy_root)
    errors: list[dict[str, str]] = []
    controls: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    tracked = _git_tracked_files(repo)
    ignored_untracked = _git_ignored_untracked_files(repo)

    for locator in _nested_git_metadata_paths(repo):
        errors.append(_error(
            "NESTED_GIT_METADATA",
            locator,
            "only the repository-root .git metadata is permitted",
        ))

    allowed_root_component = legacy_relative.parts[0]
    for item in _sorted_children(repo):
        if item.name == ".git":
            controls.append(
                _control_record(item, repo, "git-metadata", tracked)
            )
            continue
        if item.name == allowed_root_component:
            continue
        if item.name in REPO_ROOT_CONTROL_NAMES:
            controls.append(
                _control_record(item, repo, "root-control", tracked)
            )
            continue
        if WF_ID_RE.fullmatch(item.name):
            errors.append(_error(
                "DESTINATION_OVERLAP",
                item.relative_to(repo).as_posix(),
                "flat Work Folder destination already exists",
            ))
            continue
        errors.append(_error(
            "UNKNOWN_REPO_ROOT_PAYLOAD",
            item.relative_to(repo).as_posix(),
            "repo-root payload is not a classified control or legacy root",
        ))

    _validate_container_chain(
        repo,
        legacy,
        legacy_relative,
        controls,
        errors,
    )

    nested_root = legacy / DOUBLE_ROOT_RELATIVE
    nested_parent = legacy / DOUBLE_ROOT_RELATIVE.parts[0]
    if nested_parent.exists():
        if (
            nested_parent.is_symlink()
            or not nested_parent.is_dir()
            or not nested_root.is_dir()
            or nested_root.is_symlink()
        ):
            errors.append(_error(
                "INVALID_DOUBLE_ROOT",
                nested_parent.relative_to(repo).as_posix(),
                "double-root payload must be exactly 智元工作/工作记录",
            ))
        else:
            unexpected_nested = [
                item.relative_to(repo).as_posix()
                for item in _sorted_children(nested_parent)
                if item != nested_root
            ]
            for locator in unexpected_nested:
                errors.append(_error(
                    "INVALID_DOUBLE_ROOT",
                    locator,
                    "double-root parent contains unexpected payload",
                ))

    _scan_date_root(
        repo,
        legacy,
        tracked,
        controls,
        topics,
        errors,
    )
    if nested_root.is_dir() and not nested_root.is_symlink():
        # The primary scan reports 智元工作 as unknown; remove only that exact
        # diagnostic after the nested root itself has been validated.
        nested_parent_locator = nested_parent.relative_to(repo).as_posix()
        errors[:] = [
            error
            for error in errors
            if not (
                error["code"] == "UNKNOWN_LEGACY_ROOT_PAYLOAD"
                and error["locator"] == nested_parent_locator
            )
        ]
        _scan_date_root(
            repo,
            nested_root,
            tracked,
            controls,
            topics,
            errors,
        )

    locator_counts = Counter(topic["old_locator"] for topic in topics)
    for locator, count in locator_counts.items():
        if count > 1:
            errors.append(_error(
                "DUPLICATE_SOURCE_LOCATOR",
                locator,
                "primary and double-root trees contain the same logical topic",
            ))

    tombstones = _load_tombstones(
        repo,
        [
            repo / ".katana" / "tombstones.json",
            legacy / ".katana" / "tombstones.json",
            nested_root / ".katana" / "tombstones.json",
        ],
        errors,
    )

    topics.sort(key=lambda item: item["old_locator"].encode("utf-8"))
    controls.sort(key=lambda item: item["repo_relative_path"].encode("utf-8"))
    errors.sort(
        key=lambda item: (
            item["locator"].encode("utf-8"),
            item["code"],
        )
    )

    old_id_counts = Counter(
        topic["old_id"]
        for topic in topics
        if topic["old_id"] and WF_ID_RE.fullmatch(topic["old_id"])
    )
    for topic in topics:
        old_id = topic["old_id"]
        topic["canonical_id_unique"] = bool(
            old_id
            and WF_ID_RE.fullmatch(old_id)
            and old_id_counts[old_id] == 1
        )
        if old_id in tombstones:
            errors.append(_error(
                "LIVE_ID_TOMBSTONED",
                topic["old_locator"],
                f"live brief ID is tombstoned: {old_id}",
            ))

    inventory: dict[str, Any] = {
        "kind": f"{PLAN_KIND}-inventory",
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo),
        "legacy_root": str(legacy),
        "legacy_root_relative": legacy_relative.as_posix(),
        "source_head": _git_head(repo),
        "topics": topics,
        "controls": controls,
        "tombstones": tombstones,
        "ignored_untracked_files": sorted(ignored_untracked),
        "errors": errors,
        "ok": not errors,
    }
    inventory["inventory_hash"] = _hash_without_key(
        inventory,
        "inventory_hash",
    )
    return inventory


def _normalize_repairs(
    repairs: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if repairs is None:
        return {}
    if "repairs" in repairs and len(repairs) == 1:
        repairs = repairs["repairs"]
    if not isinstance(repairs, dict):
        raise MigrationError(
            "INVALID_REPAIR_METADATA",
            "repair metadata must be a mapping keyed by old locator",
        )
    normalized: dict[str, dict[str, Any]] = {}
    for locator, metadata in repairs.items():
        if not isinstance(locator, str) or not isinstance(metadata, dict):
            raise MigrationError(
                "INVALID_REPAIR_METADATA",
                "each repair entry must be an object keyed by locator",
            )
        normalized[locator] = metadata
    return normalized


def _repair_brief(
    topic: dict[str, Any],
    repair: dict[str, Any],
    new_id: str,
) -> bytes:
    state = topic["brief_state"]
    if repair.get("state") != state:
        raise MigrationError(
            "REPAIR_STATE_MISMATCH",
            f"{topic['old_locator']} expected state {state!r}",
        )
    if state != "missing":
        expected_sha = repair.get("expected_sha256")
        if expected_sha != topic["brief_sha256"]:
            raise MigrationError(
                "REPAIR_CONTENT_MISMATCH",
                f"{topic['old_locator']} repair does not bind original brief hash",
            )
    elif repair.get("expected_sha256") not in (None, ""):
        raise MigrationError(
            "REPAIR_CONTENT_MISMATCH",
            f"{topic['old_locator']} is missing and has no original brief hash",
        )

    brief_text = repair.get("brief_text")
    if not isinstance(brief_text, str):
        raise MigrationError(
            "INVALID_REPAIR_METADATA",
            f"{topic['old_locator']} repair requires brief_text",
        )
    content = brief_text.encode("utf-8")
    rewritten = _rewrite_brief_id(content, new_id)
    rewritten_frontmatter, _ = _parse_frontmatter(rewritten)
    problems = _brief_metadata_problems(
        rewritten,
        rewritten_frontmatter,
        require_id=True,
    )
    if problems:
        raise MigrationError(
            "INVALID_REPAIR_METADATA",
            (
                f"{topic['old_locator']} repair is not a valid brief: "
                + "; ".join(problems)
            ),
        )
    return rewritten


def _topic_reason(topic: dict[str, Any]) -> str:
    state = topic["brief_state"]
    if state == "missing":
        return "repair-missing-brief"
    if state == "parse_error":
        return "repair-parse-error"
    if state == "invalid_metadata":
        return "repair-invalid-metadata"
    if topic["canonical_id_unique"]:
        return "canonical-id"
    old_id = topic["old_id"]
    if old_id is None:
        return "missing-id"
    if WF_ID_RE.fullmatch(old_id):
        return "duplicate-canonical-id"
    return "legacy-id"


def _render_flat_index(mapping: list[dict[str, Any]]) -> bytes:
    entries: list[dict[str, Any]] = []
    for item in sorted(mapping, key=lambda entry: entry["new_id"]):
        content = base64.b64decode(item["brief_after_b64"])
        frontmatter, _ = _parse_frontmatter(content)
        body_match = FRONTMATTER_RE.match(content)
        assert body_match is not None
        body = body_match.group("tail").decode("utf-8")
        goal_match = GOAL_RE.search(body)
        goal = goal_match.group(1).strip() if goal_match else ""
        updated = frontmatter.get("updated", "")
        if hasattr(updated, "isoformat"):
            updated = updated.isoformat()
        else:
            updated = str(updated) if updated else ""
        entries.append({
            "updated": updated,
            "status": str(frontmatter.get("status", "")),
            "id": item["new_id"],
            "title": str(frontmatter.get("title", "")),
            "goal": goal,
        })
    entries.sort(key=lambda entry: entry["updated"], reverse=True)
    lines = [
        "# Work Folder INDEX",
        "",
        (
            f"> 共 {len(entries)} 个 work folder，按 updated 倒序。"
            "由 wf_reindex 自动生成，勿手改。"
        ),
        "",
        "| updated | status | id | title | goal |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        goal = entry["goal"].replace("|", "\\|")
        lines.append(
            f"| {entry['updated']} | {entry['status']} | {entry['id']} | "
            f"{entry['title']} | {goal} |"
        )
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _normalized_gitignore(repo: Path) -> tuple[bytes | None, bytes]:
    path = repo / ".gitignore"
    before = path.read_bytes() if path.is_file() else None
    if before is None:
        return None, f"{RUNTIME_GITIGNORE_LINE}\n".encode("utf-8")
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(
            "INVALID_GITIGNORE",
            "root .gitignore must be UTF-8 before migration",
        ) from exc
    lines = text.splitlines()
    if RUNTIME_GITIGNORE_LINE not in lines:
        lines.append(RUNTIME_GITIGNORE_LINE)
    return before, ("\n".join(lines) + "\n").encode("utf-8")


def _control_archive_destination(
    source: str,
    *,
    legacy_relative: str,
) -> tuple[str, str]:
    """Return (classification, destination) for one old control file."""

    source_path = PurePosixPath(source)
    legacy_path = PurePosixPath(legacy_relative)
    nested_path = legacy_path / DOUBLE_ROOT_RELATIVE
    root_katana = PurePosixPath(".katana")

    if source_path.parts[:1] == root_katana.parts:
        rest = PurePosixPath(*source_path.parts[1:])
        if rest.parts and rest.parts[0] == "manifests":
            tail = PurePosixPath(*rest.parts[1:])
            return (
                "legacy-manifest",
                (
                    PurePosixPath(".katana/legacy-manifests/root") / tail
                ).as_posix(),
            )
        return (
            "root-katana-control",
            (
                PurePosixPath(".katana/control-archive/root-katana") / rest
            ).as_posix(),
        )

    for physical_root, label in (
        (nested_path, "double-root"),
        (legacy_path, "primary-root"),
    ):
        try:
            rest = source_path.relative_to(physical_root)
        except ValueError:
            continue
        if rest.parts and rest.parts[0] == ".katana":
            katana_rest = PurePosixPath(*rest.parts[1:])
            if katana_rest.parts and katana_rest.parts[0] == "manifests":
                tail = PurePosixPath(*katana_rest.parts[1:])
                return (
                    "legacy-manifest",
                    (
                        PurePosixPath(
                            f".katana/legacy-manifests/{label}"
                        ) / tail
                    ).as_posix(),
                )
            return (
                "legacy-katana-control",
                (
                    PurePosixPath(
                        f".katana/control-archive/{label}/.katana"
                    ) / katana_rest
                ).as_posix(),
            )
        return (
            "legacy-root-control",
            (
                PurePosixPath(f".katana/control-archive/{label}") / rest
            ).as_posix(),
        )

    return (
        "repo-root-control",
        (
            PurePosixPath(".katana/control-archive/repo-root")
            / source_path
        ).as_posix(),
    )


def _build_control_actions(
    inventory: dict[str, Any],
    mapping: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repo = Path(inventory["repo_root"])
    legacy_relative = inventory["legacy_root_relative"]
    actions: list[dict[str, Any]] = []
    manifest_inventory: list[dict[str, Any]] = []
    action_index = 0

    def add_action(action: dict[str, Any]) -> None:
        nonlocal action_index
        action_index += 1
        action["action_id"] = f"control-{action_index:05d}"
        actions.append(action)

    regular_entries: list[dict[str, Any]] = []
    for control in inventory["controls"]:
        if control["classification"] in {
            "git-metadata",
            "legacy-container",
            "legacy-root",
        }:
            continue
        for entry in control["entries"]:
            kind = entry["kind"]
            if kind in {"directory"}:
                continue
            if kind != "regular_file":
                raise MigrationError(
                    "UNSUPPORTED_CONTROL_PAYLOAD",
                    f"control payload is not a regular file: {entry['relative_path']}",
                )
            regular_entries.append(entry)

    by_source = {
        entry["relative_path"]: entry
        for entry in regular_entries
    }
    if len(by_source) != len(regular_entries):
        raise MigrationError(
            "DUPLICATE_CONTROL_INVENTORY",
            "control inventory contains duplicate files",
        )

    in_place_writes = {".gitignore", "INDEX.md", ".katana/tombstones.json"}
    preserved_root = {".gitkeep"}
    for source, entry in sorted(
        by_source.items(),
        key=lambda pair: pair[0].encode("utf-8"),
    ):
        if source in preserved_root:
            continue
        classification, destination = _control_archive_destination(
            source,
            legacy_relative=legacy_relative,
        )
        if source in in_place_writes:
            kind = "copy"
        else:
            kind = "move"
        add_action({
            "kind": kind,
            "classification": classification,
            "source_repo_path": source,
            "destination_repo_path": destination,
            "source_sha256": entry["sha256"],
            "size": entry["size"],
            "git_tracked": entry["git_tracked"],
        })
        if classification == "legacy-manifest":
            manifest_inventory.append({
                "source_repo_path": source,
                "archive_repo_path": destination,
                "sha256": entry["sha256"],
                "size": entry["size"],
                "git_tracked": entry["git_tracked"],
            })

    before_gitignore, after_gitignore = _normalized_gitignore(repo)
    generated: list[tuple[str, bytes | None, bytes, str]] = [
        (
            ".gitignore",
            before_gitignore,
            after_gitignore,
            "runtime-ignore",
        ),
        (
            "INDEX.md",
            (repo / "INDEX.md").read_bytes()
            if (repo / "INDEX.md").is_file()
            else None,
            _render_flat_index(mapping),
            "flat-index",
        ),
        (
            ".katana/tombstones.json",
            (repo / ".katana/tombstones.json").read_bytes()
            if (repo / ".katana/tombstones.json").is_file()
            else None,
            canonical_json({"tombstones": inventory.get("tombstones") or []}),
            "merged-tombstones",
        ),
        (
            ".katana/flat-layout.json",
            None,
            canonical_json(FLAT_LAYOUT_PAYLOAD),
            "flat-layout-canary",
        ),
        (
            ".katana/legacy-manifest-inventory.json",
            None,
            canonical_json({
                "schema_version": 1,
                "manifests": manifest_inventory,
            }),
            "legacy-manifest-inventory",
        ),
    ]
    occupied_destinations = {
        action["destination_repo_path"]
        for action in actions
    }
    for destination, before, after, classification in generated:
        target = repo / destination
        if (
            destination not in in_place_writes
            and (target.exists() or target.is_symlink())
        ):
            raise MigrationError(
                "CONTROL_DESTINATION_OVERLAP",
                f"generated control destination already exists: {destination}",
            )
        if destination in occupied_destinations:
            raise MigrationError(
                "CONTROL_DESTINATION_COLLISION",
                f"control actions collide at: {destination}",
            )
        add_action({
            "kind": "write",
            "classification": classification,
            "source_repo_path": None,
            "destination_repo_path": destination,
            "before_sha256": sha256_bytes(before) if before is not None else None,
            "after_sha256": sha256_bytes(after),
            "size_after": len(after),
            "content_b64": base64.b64encode(after).decode("ascii"),
        })

    destinations = [
        action["destination_repo_path"]
        for action in actions
    ]
    if len(destinations) != len(set(destinations)):
        raise MigrationError(
            "CONTROL_DESTINATION_COLLISION",
            "control actions contain duplicate destinations",
        )
    return actions


def build_plan(
    inventory: dict[str, Any],
    *,
    repairs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, content-addressed flat migration plan."""

    if (
        inventory.get("kind") != f"{PLAN_KIND}-inventory"
        or inventory.get("schema_version") != SCHEMA_VERSION
    ):
        raise MigrationError(
            "INVALID_INVENTORY",
            "inventory kind is not recognized",
        )
    expected_inventory_hash = _hash_without_key(
        inventory,
        "inventory_hash",
    )
    if inventory.get("inventory_hash") != expected_inventory_hash:
        raise MigrationError(
            "INVENTORY_HASH_MISMATCH",
            "inventory bytes do not match inventory_hash",
        )
    if not inventory.get("ok"):
        errors = inventory.get("errors") or []
        first_code = errors[0].get("code", "INVENTORY_REJECTED")
        raise MigrationError(
            first_code,
            "inventory contains fail-closed structural errors",
            details={"errors": errors},
        )

    repair_map = _normalize_repairs(repairs)
    topics = sorted(
        inventory["topics"],
        key=lambda item: item["old_locator"].encode("utf-8"),
    )
    topic_locators = {topic["old_locator"] for topic in topics}
    unknown_repairs = sorted(set(repair_map) - topic_locators)
    if unknown_repairs:
        raise MigrationError(
            "UNUSED_REPAIR_METADATA",
            "repair metadata contains unknown locators",
            details={"locators": unknown_repairs},
        )

    reserved = {
        topic["old_id"]
        for topic in topics
        if topic["old_id"] and WF_ID_RE.fullmatch(topic["old_id"])
    }
    reserved.update(inventory.get("tombstones") or [])
    mapping: list[dict[str, Any]] = []
    expected_diff_paths: set[str] = set()

    for topic in topics:
        locator = topic["old_locator"]
        state = topic["brief_state"]
        if state in REPAIR_STATES and locator not in repair_map:
            raise MigrationError(
                "REPAIR_METADATA_REQUIRED",
                f"{locator} ({state}) requires explicit repair metadata",
            )
        if state == "valid" and locator in repair_map:
            raise MigrationError(
                "UNUSED_REPAIR_METADATA",
                f"{locator} has a valid brief and must not be replaced",
            )

        if topic["canonical_id_unique"]:
            new_id = topic["old_id"]
            collision_counter = None
        else:
            new_id, collision_counter = deterministic_id(locator, reserved)
            reserved.add(new_id)

        if state in REPAIR_STATES:
            brief_after = _repair_brief(
                topic,
                repair_map[locator],
                new_id,
            )
        else:
            encoded = topic.get("brief_content_b64")
            if not encoded:
                raise MigrationError(
                    "BRIEF_CONTENT_MISSING",
                    f"{locator} valid brief bytes are missing from inventory",
                )
            brief_before = base64.b64decode(encoded)
            if topic["canonical_id_unique"]:
                brief_after = brief_before
            else:
                brief_after = _rewrite_brief_id(brief_before, new_id)

        hashes: list[dict[str, Any]] = []
        saw_brief = False
        destination_paths: set[str] = set()
        for file_record in topic["files"]:
            source_relative_path = file_record["relative_path"]
            relative_path = file_record["destination_relative_path"]
            if relative_path in destination_paths:
                raise MigrationError(
                    "DESTINATION_FILE_COLLISION",
                    f"{locator} maps multiple files to {relative_path}",
                )
            destination_paths.add(relative_path)
            before_hash = file_record["sha256"]
            before_size = file_record["size"]
            if source_relative_path == "_brief.md":
                saw_brief = True
                after_hash = sha256_bytes(brief_after)
                after_size = len(brief_after)
            else:
                after_hash = before_hash
                after_size = before_size
            hashes.append({
                "source_relative_path": source_relative_path,
                "relative_path": relative_path,
                "before_sha256": before_hash,
                "after_sha256": after_hash,
                "size_before": before_size,
                "size_after": after_size,
                "git_tracked": file_record["git_tracked"],
                "content_kind": file_record["content_kind"],
                "api_tool": file_record["api_tool"],
            })
        if not saw_brief:
            hashes.append({
                "source_relative_path": "_brief.md",
                "relative_path": "_brief.md",
                "before_sha256": None,
                "after_sha256": sha256_bytes(brief_after),
                "size_before": None,
                "size_after": len(brief_after),
                "git_tracked": False,
                "content_kind": "utf8_text",
                "api_tool": "fs_read",
            })
        hashes.sort(key=lambda item: item["relative_path"].encode("utf-8"))

        source_repo_path = topic["source_repo_path"]
        for hash_record in hashes:
            source_relative_path = hash_record["source_relative_path"]
            relative_path = hash_record["relative_path"]
            if (
                hash_record["before_sha256"] is not None
                and hash_record["git_tracked"]
            ):
                expected_diff_paths.add(
                    f"{source_repo_path}/{source_relative_path}"
                )
            expected_diff_paths.add(f"{new_id}/{relative_path}")

        mapping.append({
            "old_locator": locator,
            "old_repo_path": source_repo_path,
            "old_id": topic["old_id"],
            "new_id": new_id,
            "new_repo_path": new_id,
            "reason": _topic_reason(topic),
            "collision_counter": collision_counter,
            "brief_state": state,
            "brief_after_b64": base64.b64encode(brief_after).decode("ascii"),
            "content_hashes": hashes,
        })

    if len(mapping) != len(topics):
        raise MigrationError(
            "INCOMPLETE_MAP",
            "plan map must cover every topic exactly once",
        )
    new_ids = [item["new_id"] for item in mapping]
    if len(new_ids) != len(set(new_ids)):
        raise MigrationError(
            "DUPLICATE_DESTINATION_ID",
            "planned destination IDs are not unique",
        )

    control_actions = _build_control_actions(inventory, mapping)
    for action in control_actions:
        source = action.get("source_repo_path")
        if action["kind"] == "move" and action["git_tracked"]:
            expected_diff_paths.add(source)
        if action["kind"] == "copy":
            expected_diff_paths.add(action["destination_repo_path"])
        elif action["kind"] == "move":
            expected_diff_paths.add(action["destination_repo_path"])
        elif (
            action["before_sha256"] is None
            or action["before_sha256"] != action["after_sha256"]
        ):
            expected_diff_paths.add(action["destination_repo_path"])

    plan: dict[str, Any] = {
        "kind": PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "repo_root": inventory["repo_root"],
        "legacy_root": inventory["legacy_root"],
        "legacy_root_relative": inventory["legacy_root_relative"],
        "source_head": inventory["source_head"],
        "inventory_hash": inventory["inventory_hash"],
        "controls": inventory["controls"],
        "control_actions": control_actions,
        "tombstones": inventory.get("tombstones") or [],
        "ignored_untracked_files": inventory.get("ignored_untracked_files") or [],
        "map": mapping,
        "expected_diff_paths": sorted(
            expected_diff_paths,
            key=lambda value: value.encode("utf-8"),
        ),
    }
    plan["plan_hash"] = _hash_without_key(plan, "plan_hash")
    _validate_plan(plan)
    return plan


def maintenance_sentinel_payload(
    plan: dict[str, Any],
    *,
    expected_plan_hash: str | None = None,
) -> dict[str, Any]:
    if (
        expected_plan_hash is not None
        and expected_plan_hash != plan.get("plan_hash")
    ):
        raise MigrationError(
            "PLAN_HASH_CAS_MISMATCH",
            "approved plan hash does not match the plan artifact",
        )
    return {
        "mode": MAINTENANCE_MODE,
        "repo_root": plan["repo_root"],
        "legacy_root": plan["legacy_root"],
        "expected_head": plan["source_head"],
        "plan_hash": plan["plan_hash"],
    }


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    path = PurePosixPath(value)
    return bool(
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _valid_old_locator(locator: Any) -> bool:
    if not _is_safe_relative_path(locator):
        return False
    parts = locator.split("/")
    if len(parts) != 4:
        return False
    year, month, day, topic = parts
    if not (
        YEAR_RE.fullmatch(year)
        and MONTH_RE.fullmatch(month)
        and DAY_RE.fullmatch(day)
        and topic
    ):
        return False
    try:
        dt.date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def _invalid_plan(message: str) -> None:
    raise MigrationError("INVALID_PLAN", message)


def _validate_plan_controls(
    controls: Any,
    *,
    legacy_relative: str,
) -> None:
    if not isinstance(controls, list):
        _invalid_plan("plan controls must be a list")
    seen: set[str] = set()
    legacy_parts = PurePosixPath(legacy_relative).parts
    nested_legacy_relative = (
        PurePosixPath(legacy_relative) / DOUBLE_ROOT_RELATIVE
    ).as_posix()
    container_paths = {
        PurePosixPath(*legacy_parts[:index]).as_posix()
        for index in range(1, len(legacy_parts))
    }
    for control in controls:
        if not isinstance(control, dict):
            _invalid_plan("plan control entries must be objects")
        path = control.get("repo_relative_path")
        classification = control.get("classification")
        entries = control.get("entries")
        if not _is_safe_relative_path(path) or path in seen:
            _invalid_plan("plan control paths must be safe and unique")
        seen.add(path)
        if not isinstance(entries, list):
            _invalid_plan("plan control entries must contain an entries list")

        valid_location = (
            (classification == "git-metadata" and path == ".git")
            or (
                classification == "root-control"
                and path in REPO_ROOT_CONTROL_NAMES
            )
            or (
                classification == "legacy-root-control"
                and path
                in {
                    f"{legacy_relative}/{name}"
                    for name in LEGACY_ROOT_CONTROL_NAMES
                } | {
                    f"{nested_legacy_relative}/{name}"
                    for name in LEGACY_ROOT_CONTROL_NAMES
                }
            )
            or (
                classification == "legacy-container"
                and path in container_paths
                and entries == []
            )
            or (
                classification == "legacy-root"
                and path == legacy_relative
                and entries == []
            )
        )
        if not valid_location:
            _invalid_plan("plan contains an unknown or misplaced control")

        for entry in entries:
            if not isinstance(entry, dict):
                _invalid_plan("plan control inventory entries must be objects")
            entry_path = entry.get("relative_path")
            if (
                not _is_safe_relative_path(entry_path)
                or (
                    entry_path != path
                    and not entry_path.startswith(f"{path}/")
                )
            ):
                _invalid_plan("plan control inventory path escapes its control")
            if entry.get("kind") not in {
                "directory",
                "regular_file",
                "symlink",
                "special",
            }:
                _invalid_plan("plan control inventory kind is unknown")


def _validate_plan_control_actions(
    actions: Any,
    controls: list[dict[str, Any]],
) -> set[str]:
    if not isinstance(actions, list) or not actions:
        _invalid_plan("plan control_actions must be a non-empty list")
    inventory_files = {
        entry["relative_path"]: entry
        for control in controls
        for entry in control["entries"]
        if entry.get("kind") == "regular_file"
    }
    ids: set[str] = set()
    destinations: set[str] = set()
    expected_diff: set[str] = set()
    generated_destinations = {
        ".gitignore",
        "INDEX.md",
        ".katana/tombstones.json",
        ".katana/flat-layout.json",
        ".katana/legacy-manifest-inventory.json",
    }
    for action in actions:
        if not isinstance(action, dict):
            _invalid_plan("control actions must be objects")
        action_id = action.get("action_id")
        if (
            not isinstance(action_id, str)
            or not re.fullmatch(r"control-[0-9]{5}", action_id)
            or action_id in ids
        ):
            _invalid_plan("control action IDs must be canonical and unique")
        ids.add(action_id)
        kind = action.get("kind")
        if kind not in {"copy", "move", "write"}:
            _invalid_plan("control action kind is unknown")
        destination = action.get("destination_repo_path")
        if (
            not _is_safe_relative_path(destination)
            or destination in destinations
        ):
            _invalid_plan("control action destinations must be safe and unique")
        destinations.add(destination)

        if kind in {"copy", "move"}:
            source = action.get("source_repo_path")
            if not _is_safe_relative_path(source) or source not in inventory_files:
                _invalid_plan("control action source is not inventoried")
            inventory_entry = inventory_files[source]
            if (
                action.get("source_sha256") != inventory_entry.get("sha256")
                or action.get("size") != inventory_entry.get("size")
                or action.get("git_tracked") != inventory_entry.get("git_tracked")
            ):
                _invalid_plan("control action source metadata changed")
            if not (
                destination.startswith(".katana/control-archive/")
                or destination.startswith(".katana/legacy-manifests/")
            ):
                _invalid_plan("archived control destination is outside governance")
            if kind == "move" and action["git_tracked"]:
                expected_diff.add(source)
            expected_diff.add(destination)
            continue

        if action.get("source_repo_path") is not None:
            _invalid_plan("write control action must not have a source")
        if destination not in generated_destinations:
            _invalid_plan("write control destination is not governed")
        encoded = action.get("content_b64")
        if not isinstance(encoded, str):
            _invalid_plan("write control content must be base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MigrationError(
                "INVALID_PLAN",
                "write control content is not valid base64",
            ) from exc
        before_hash = action.get("before_sha256")
        if before_hash is not None and (
            not isinstance(before_hash, str)
            or not SHA256_RE.fullmatch(before_hash)
        ):
            _invalid_plan("write control before hash is invalid")
        if (
            action.get("after_sha256") != sha256_bytes(content)
            or action.get("size_after") != len(content)
        ):
            _invalid_plan("write control content does not match its hash")
        if before_hash is None or before_hash != action["after_sha256"]:
            expected_diff.add(destination)
    return expected_diff


def _validate_plan_item(
    item: Any,
    *,
    legacy_relative: str,
) -> set[str]:
    if not isinstance(item, dict):
        _invalid_plan("every plan map entry must be an object")

    locator = item.get("old_locator")
    if not _valid_old_locator(locator):
        _invalid_plan("plan contains an invalid YYYY/MM/DD/topic locator")
    old_repo_path = item.get("old_repo_path")
    expected_old_paths = {
        f"{legacy_relative}/{locator}",
        (
            f"{legacy_relative}/{DOUBLE_ROOT_RELATIVE.as_posix()}/"
            f"{locator}"
        ),
    }
    if old_repo_path not in expected_old_paths:
        _invalid_plan("planned source path does not match its old locator")

    new_id = item.get("new_id")
    if not isinstance(new_id, str) or not WF_ID_RE.fullmatch(new_id):
        _invalid_plan("planned destination ID is not canonical")
    if item.get("new_repo_path") != new_id:
        _invalid_plan("planned destination must be a repo-root wf-* directory")
    if item.get("reason") not in MIGRATION_REASONS:
        _invalid_plan("plan contains an unknown migration reason")
    if item.get("brief_state") not in {"valid", *REPAIR_STATES}:
        _invalid_plan("plan contains an unknown brief state")
    if item.get("old_id") is not None and not isinstance(item.get("old_id"), str):
        _invalid_plan("old ID must be a string or null")
    counter = item.get("collision_counter")
    if counter is not None and (
        type(counter) is not int or counter < 0
    ):
        _invalid_plan("collision counter must be a non-negative integer or null")

    hashes = item.get("content_hashes")
    if not isinstance(hashes, list) or not hashes:
        _invalid_plan("plan entry has no content hashes")
    by_path: dict[str, dict[str, Any]] = {}
    source_paths: set[str] = set()
    expected_diff: set[str] = set()
    for record in hashes:
        if not isinstance(record, dict):
            _invalid_plan("content hash entries must be objects")
        relative_path = record.get("relative_path")
        if not _is_safe_relative_path(relative_path):
            _invalid_plan("content hash contains an unsafe relative path")
        source_relative_path = record.get("source_relative_path")
        if not _is_safe_relative_path(source_relative_path):
            _invalid_plan("content hash contains an unsafe source relative path")
        if relative_path in by_path:
            _invalid_plan("content hash paths must be unique per topic")
        if source_relative_path in source_paths:
            _invalid_plan("content source paths must be unique per topic")
        source_paths.add(source_relative_path)
        if any(
            part in RESERVED_TOPIC_SEGMENTS
            for part in PurePosixPath(relative_path).parts
        ):
            _invalid_plan("destination content contains a reserved path segment")
        if type(record.get("git_tracked")) is not bool:
            _invalid_plan("content git_tracked must be boolean")
        if record.get("content_kind") not in {"utf8_text", "binary"}:
            _invalid_plan("content kind is unknown")
        expected_api = (
            "fs_read"
            if record["content_kind"] == "utf8_text"
            else "fs_read_bytes"
        )
        if record.get("api_tool") != expected_api:
            _invalid_plan("content API reachability classification is invalid")

        before_hash = record.get("before_sha256")
        after_hash = record.get("after_sha256")
        before_size = record.get("size_before")
        after_size = record.get("size_after")
        if before_hash is None:
            if before_size is not None or relative_path != "_brief.md":
                _invalid_plan("only a missing _brief.md may lack a before hash")
        elif (
            not isinstance(before_hash, str)
            or not SHA256_RE.fullmatch(before_hash)
            or type(before_size) is not int
            or before_size < 0
        ):
            _invalid_plan("content before hash or size is invalid")
        if (
            not isinstance(after_hash, str)
            or not SHA256_RE.fullmatch(after_hash)
            or type(after_size) is not int
            or after_size < 0
        ):
            _invalid_plan("content after hash or size is invalid")

        by_path[relative_path] = record
        if before_hash is not None and record["git_tracked"]:
            expected_diff.add(f"{old_repo_path}/{source_relative_path}")
        expected_diff.add(f"{new_id}/{relative_path}")

    brief_record = by_path.get("_brief.md")
    if brief_record is None:
        _invalid_plan("every destination requires a planned _brief.md")
    encoded = item.get("brief_after_b64")
    if not isinstance(encoded, str):
        _invalid_plan("planned brief content must be base64 text")
    try:
        brief_after = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MigrationError(
            "INVALID_PLAN",
            "planned brief content is not valid base64",
        ) from exc
    if (
        sha256_bytes(brief_after) != brief_record["after_sha256"]
        or len(brief_after) != brief_record["size_after"]
    ):
        _invalid_plan("planned brief bytes do not match content hashes")
    try:
        frontmatter, _ = _parse_frontmatter(brief_after)
        problems = _brief_metadata_problems(
            brief_after,
            frontmatter,
            require_id=True,
        )
    except MigrationError as exc:
        raise MigrationError(
            "INVALID_PLAN",
            "planned brief is not parseable",
        ) from exc
    if problems or str(frontmatter.get("id") or "") != new_id:
        _invalid_plan("planned brief metadata does not match destination ID")
    if brief_record["source_relative_path"] != "_brief.md":
        _invalid_plan("planned brief source must be _brief.md")
    return expected_diff


def _planned_untracked_source_records(
    plan: dict[str, Any],
) -> dict[str, tuple[str, int]]:
    records: dict[str, tuple[str, int]] = {}
    for item in plan.get("map") or []:
        for record in item.get("content_hashes") or []:
            if record.get("before_sha256") is None or record.get("git_tracked"):
                continue
            source = (
                f"{item['old_repo_path']}/"
                f"{record['source_relative_path']}"
            )
            records[source] = (
                record["before_sha256"],
                record["size_before"],
            )
    for action in plan.get("control_actions") or []:
        if (
            action.get("kind") not in {"copy", "move"}
            or action.get("git_tracked")
        ):
            continue
        records[action["source_repo_path"]] = (
            action["source_sha256"],
            action["size"],
        )
    return records


def _validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("kind") != PLAN_KIND:
        raise MigrationError("INVALID_PLAN", "plan kind is not recognized")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise MigrationError(
            "INVALID_PLAN",
            "plan schema version is not supported",
        )
    expected_hash = _hash_without_key(plan, "plan_hash")
    if plan.get("plan_hash") != expected_hash:
        raise MigrationError(
            "PLAN_HASH_MISMATCH",
            "plan bytes do not match plan_hash",
        )
    if not isinstance(plan.get("repo_root"), str) or not Path(
        plan["repo_root"],
    ).is_absolute():
        _invalid_plan("plan repo root must be absolute")
    if not isinstance(plan.get("legacy_root"), str) or not Path(
        plan["legacy_root"],
    ).is_absolute():
        _invalid_plan("plan legacy root must be absolute")
    legacy_relative = plan.get("legacy_root_relative")
    if not _is_safe_relative_path(legacy_relative):
        _invalid_plan("plan legacy root relative path is invalid")
    if Path(plan["repo_root"], legacy_relative) != Path(plan["legacy_root"]):
        _invalid_plan("plan legacy root does not match its repo-relative path")
    if not isinstance(plan.get("source_head"), str) or not GIT_OBJECT_RE.fullmatch(
        plan["source_head"],
    ):
        _invalid_plan("plan source HEAD is invalid")
    if not isinstance(plan.get("inventory_hash"), str) or not SHA256_RE.fullmatch(
        plan["inventory_hash"],
    ):
        _invalid_plan("plan inventory hash is invalid")
    _validate_plan_controls(
        plan.get("controls"),
        legacy_relative=legacy_relative,
    )
    expected_diff = _validate_plan_control_actions(
        plan.get("control_actions"),
        plan["controls"],
    )
    mapping = plan.get("map")
    if not isinstance(mapping, list) or not mapping:
        raise MigrationError("INVALID_PLAN", "plan map is empty")
    for item in mapping:
        expected_diff.update(
            _validate_plan_item(
                item,
                legacy_relative=legacy_relative,
            )
        )
    if len({item.get("old_locator") for item in mapping}) != len(mapping):
        raise MigrationError(
            "INVALID_PLAN",
            "plan contains duplicate old locators",
        )
    if len({item.get("new_id") for item in mapping}) != len(mapping):
        raise MigrationError(
            "INVALID_PLAN",
            "plan contains duplicate destination IDs",
        )
    ignored_sources = plan.get("ignored_untracked_files")
    if (
        not isinstance(ignored_sources, list)
        or any(not _is_safe_relative_path(path) for path in ignored_sources)
        or len(ignored_sources) != len(set(ignored_sources))
        or set(ignored_sources) != set(_planned_untracked_source_records(plan))
    ):
        _invalid_plan(
            "plan ignored-untracked sources are incomplete or unsafe"
        )
    expected_diff_paths = sorted(
        expected_diff,
        key=lambda value: value.encode("utf-8"),
    )
    if plan.get("expected_diff_paths") != expected_diff_paths:
        _invalid_plan("plan expected Git diff paths are incomplete or unsafe")


def _current_topic_files(topic: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for item in sorted(topic.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(topic).as_posix()
        if item.is_symlink():
            raise MigrationError(
                "SYMLINK_IN_TOPIC",
                f"topic contains symlink: {item}",
            )
        if item.is_dir():
            continue
        if not item.is_file():
            raise MigrationError(
                "SPECIAL_FILE_IN_TOPIC",
                f"topic contains special file: {item}",
            )
        content = item.read_bytes()
        records[relative] = {
            "sha256": sha256_bytes(content),
            "size": len(content),
        }
    return records


def _validate_pre_apply_content(
    plan: dict[str, Any],
    repo_root: Path,
) -> None:
    for item in plan["map"]:
        source = repo_root / item["old_repo_path"]
        destination = repo_root / item["new_repo_path"]
        if not source.is_dir():
            raise MigrationError(
                "SOURCE_ANCHOR_MISSING",
                f"planned source directory is missing: {source}",
            )
        if destination.exists():
            raise MigrationError(
                "DESTINATION_OVERLAP",
                f"planned destination already exists: {destination}",
            )
        current = _current_topic_files(source)
        expected = {
            record["source_relative_path"]: record
            for record in item["content_hashes"]
            if record["before_sha256"] is not None
        }
        if set(current) != set(expected):
            raise MigrationError(
                "SOURCE_FILESET_MISMATCH",
                f"source files changed after planning: {item['old_locator']}",
            )
        for relative_path, record in expected.items():
            if current[relative_path]["sha256"] != record["before_sha256"]:
                raise MigrationError(
                    "SOURCE_HASH_MISMATCH",
                    (
                        f"source content changed after planning: "
                        f"{item['old_locator']}/{relative_path}"
                    ),
                )


def _validate_apply_gates(
    plan: dict[str, Any],
    repo_root: Path,
    legacy_root: Path,
    *,
    expected_head: str,
    expected_plan_hash: str,
    maintenance_sentinel: str | os.PathLike[str],
) -> None:
    _validate_plan(plan)
    if str(repo_root) != plan["repo_root"] or str(legacy_root) != plan["legacy_root"]:
        raise MigrationError(
            "PLAN_ROOT_MISMATCH",
            "explicit roots do not match the plan",
        )
    if expected_plan_hash != plan["plan_hash"]:
        raise MigrationError(
            "PLAN_HASH_CAS_MISMATCH",
            "expected plan hash does not match the plan",
        )
    if expected_head != plan["source_head"]:
        raise MigrationError(
            "HEAD_CAS_MISMATCH",
            "expected HEAD does not match the plan source HEAD",
        )
    current_head = _git_head(repo_root)
    if current_head != expected_head:
        raise MigrationError(
            "HEAD_CAS_MISMATCH",
            f"current HEAD {current_head} does not match {expected_head}",
        )
    status = str(
        _git(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    )
    if status:
        raise MigrationError(
            "GIT_NOT_CLEAN",
            "apply requires a clean Git working tree",
            details={"status": status.splitlines()},
        )

    sentinel = Path(maintenance_sentinel).expanduser().resolve()
    try:
        sentinel.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise MigrationError(
            "INVALID_MAINTENANCE_SENTINEL",
            "maintenance sentinel must live outside the Git repository",
        )
    if not sentinel.is_file():
        raise MigrationError(
            "MAINTENANCE_SENTINEL_MISSING",
            f"maintenance sentinel is missing: {sentinel}",
        )
    try:
        sentinel_data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_MAINTENANCE_SENTINEL",
            "maintenance sentinel is not valid JSON",
        ) from exc
    if sentinel_data != maintenance_sentinel_payload(plan):
        raise MigrationError(
            "INVALID_MAINTENANCE_SENTINEL",
            "maintenance sentinel is not bound to this plan and HEAD",
        )

    current_inventory = build_inventory(repo_root, legacy_root)
    if current_inventory["inventory_hash"] != plan["inventory_hash"]:
        raise MigrationError(
            "INVENTORY_CAS_MISMATCH",
            "repository inventory changed after planning",
            details={
                "expected_inventory_hash": plan["inventory_hash"],
                "actual_inventory_hash": current_inventory["inventory_hash"],
                "inventory_errors": current_inventory["errors"],
            },
        )

    _validate_pre_apply_content(plan, repo_root)


def _discover_source_anchors(legacy_root: Path) -> list[str]:
    anchors: list[str] = []
    if not legacy_root.is_dir():
        return anchors
    roots = [legacy_root]
    nested_root = legacy_root / DOUBLE_ROOT_RELATIVE
    if nested_root.is_dir() and not nested_root.is_symlink():
        roots.append(nested_root)
    for source_root in roots:
        anchors.extend(_discover_source_anchors_in_root(source_root, legacy_root))
    return sorted(anchors, key=lambda value: value.encode("utf-8"))


def _discover_source_anchors_in_root(
    source_root: Path,
    display_root: Path,
) -> list[str]:
    anchors: list[str] = []
    for year_entry in _sorted_children(source_root):
        if not (
            YEAR_RE.fullmatch(year_entry.name)
            and year_entry.is_dir()
            and not year_entry.is_symlink()
        ):
            continue
        for month_entry in _sorted_children(year_entry):
            if not (
                MONTH_RE.fullmatch(month_entry.name)
                and month_entry.is_dir()
                and not month_entry.is_symlink()
            ):
                continue
            for day_entry in _sorted_children(month_entry):
                if not (
                    DAY_RE.fullmatch(day_entry.name)
                    and day_entry.is_dir()
                    and not day_entry.is_symlink()
                ):
                    continue
                for topic_entry in _sorted_children(day_entry):
                    if topic_entry.is_dir() and not topic_entry.is_symlink():
                        anchors.append(
                            topic_entry.relative_to(display_root).as_posix()
                        )
    return anchors


def _git_diff_paths(repo_root: Path) -> set[str]:
    raw = _git(
        repo_root,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        "HEAD",
        "--",
        text=False,
    )
    assert isinstance(raw, bytes)
    tracked_changes = {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }
    return (
        tracked_changes
        | _git_untracked_files(repo_root)
        | _git_ignored_untracked_files(repo_root)
    )


def _verify_planned_controls(
    plan: dict[str, Any],
    repo_root: Path,
) -> None:
    if not (repo_root / ".git").is_dir():
        raise MigrationError(
            "CONTROL_CHANGED",
            "Git metadata disappeared during migration",
        )
    for action in plan["control_actions"]:
        destination = repo_root / action["destination_repo_path"]
        if destination.is_symlink() or not destination.is_file():
            raise MigrationError(
                "CONTROL_CHANGED",
                f"planned control destination is missing: {destination}",
            )
        content = destination.read_bytes()
        expected_hash = (
            action["source_sha256"]
            if action["kind"] in {"copy", "move"}
            else action["after_sha256"]
        )
        if sha256_bytes(content) != expected_hash:
            raise MigrationError(
                "CONTROL_CHANGED",
                f"planned control hash changed: {destination}",
            )
        if action["kind"] == "move":
            source = repo_root / action["source_repo_path"]
            if source.exists() or source.is_symlink():
                raise MigrationError(
                    "CONTROL_CHANGED",
                    f"archived control source remains: {source}",
                )
    if (repo_root / ".katana/manifests").exists():
        raise MigrationError(
            "CONTROL_CHANGED",
            "legacy tracked manifest directory remains active",
        )


def _verify_api_reachability(
    plan: dict[str, Any],
    repo_root: Path,
) -> dict[str, int]:
    """Read every migrated payload through its governed public API."""

    from katana_kernel import (  # noqa: PLC0415 - standalone script
        GovernedKernel,
        GovernedVFS,
        ResourceIdLedger,
        TransactionManifest,
    )
    from katana_work_folder_mcp.fs_tools import FSTools  # noqa: PLC0415
    from katana_work_folder_mcp.store import _wf_policy  # noqa: PLC0415

    kernel = GovernedKernel()
    kernel.bind(
        "work-folder",
        _wf_policy(),
        GovernedVFS(str(repo_root)),
        ResourceIdLedger(
            str(repo_root / ".katana/tombstones.json"),
            prefix="wf-",
        ),
        TransactionManifest(
            str(repo_root / ".katana/runtime/manifests"),
            git_tracked=False,
        ),
        str(repo_root),
    )
    tools = FSTools(kernel, str(repo_root))
    counts = {"fs_read": 0, "fs_read_bytes": 0}
    for item in plan["map"]:
        for record in item["content_hashes"]:
            filename = record["relative_path"]
            if record["api_tool"] == "fs_read":
                result = tools.fs_read(item["new_id"], filename, limit=1)
            else:
                result = tools.fs_read_bytes(
                    item["new_id"],
                    filename,
                    limit=1,
                )
            if not result.get("ok"):
                raise MigrationError(
                    "API_REACHABILITY_FAILED",
                    (
                        f"{item['new_id']}/{filename} is not reachable through "
                        f"{record['api_tool']}: {result.get('code')}"
                    ),
                )
            counts[record["api_tool"]] += 1
    return counts


def verify_plan(
    plan: dict[str, Any],
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Verify the migrated working tree against a frozen plan."""

    _validate_plan(plan)
    repo, legacy, _ = _validate_roots(
        repo_root,
        legacy_root,
        require_legacy=False,
    )
    if str(repo) != plan["repo_root"] or str(legacy) != plan["legacy_root"]:
        raise MigrationError(
            "PLAN_ROOT_MISMATCH",
            "explicit roots do not match the plan",
        )
    if _git_head(repo) != plan["source_head"]:
        raise MigrationError(
            "HEAD_CAS_MISMATCH",
            "HEAD changed before the migration was committed",
        )
    nested_git = _nested_git_metadata_paths(repo)
    if nested_git:
        raise MigrationError(
            "NESTED_GIT_METADATA",
            "migrated repository contains nested Git metadata",
            details={"paths": nested_git},
        )

    _verify_planned_controls(plan, repo)

    ids: list[str] = []
    for item in plan["map"]:
        source = repo / item["old_repo_path"]
        destination = repo / item["new_repo_path"]
        if source.exists():
            raise MigrationError(
                "SOURCE_ANCHOR_REMAINS",
                f"source topic still exists: {source}",
            )
        if not destination.is_dir():
            raise MigrationError(
                "DESTINATION_MISSING",
                f"destination topic is missing: {destination}",
            )
        if destination.name != item["new_id"]:
            raise MigrationError(
                "DESTINATION_ID_MISMATCH",
                f"directory name does not match planned ID: {destination}",
            )

        current = _current_topic_files(destination)
        expected = {
            record["relative_path"]: record
            for record in item["content_hashes"]
        }
        if set(current) != set(expected):
            raise MigrationError(
                "POST_FILESET_MISMATCH",
                f"destination files differ from plan: {item['new_id']}",
            )
        for relative_path, record in expected.items():
            if current[relative_path]["sha256"] != record["after_sha256"]:
                raise MigrationError(
                    "POST_HASH_MISMATCH",
                    f"destination hash differs: {item['new_id']}/{relative_path}",
                )

        before_multiset = sorted(
            record["before_sha256"]
            for record in item["content_hashes"]
            if (
                record["relative_path"] != "_brief.md"
                and record["before_sha256"] is not None
            )
        )
        after_multiset = sorted(
            current[record["relative_path"]]["sha256"]
            for record in item["content_hashes"]
            if record["relative_path"] != "_brief.md"
        )
        if before_multiset != after_multiset:
            raise MigrationError(
                "REGULAR_FILE_MULTISET_MISMATCH",
                f"non-brief content changed: {item['new_id']}",
            )

        brief_content = (destination / "_brief.md").read_bytes()
        frontmatter, _ = _parse_frontmatter(brief_content)
        actual_id = str(frontmatter.get("id") or "")
        if actual_id != item["new_id"]:
            raise MigrationError(
                "BRIEF_ID_MISMATCH",
                (
                    f"{item['new_id']} directory contains brief id "
                    f"{actual_id!r}"
                ),
            )
        ids.append(actual_id)

    if len(ids) != len(set(ids)):
        raise MigrationError(
            "DUPLICATE_DESTINATION_ID",
            "migrated brief IDs are not unique",
        )

    source_anchors = _discover_source_anchors(legacy)
    if source_anchors:
        raise MigrationError(
            "SOURCE_ANCHOR_REMAINS",
            "legacy YYYY/MM/DD/topic anchors remain after apply",
            details={"anchors": source_anchors},
        )

    actual_diff = _git_diff_paths(repo)
    expected_diff = set(plan["expected_diff_paths"])
    unexpected = sorted(actual_diff - expected_diff)
    missing = sorted(expected_diff - actual_diff)
    if unexpected or missing:
        raise MigrationError(
            "UNEXPECTED_GIT_DIFF",
            "Git diff does not exactly match the migration plan",
            details={
                "unexpected": unexpected,
                "missing": missing,
            },
        )

    api_reachability = _verify_api_reachability(plan, repo)
    return {
        "ok": True,
        "plan_hash": plan["plan_hash"],
        "source_head": plan["source_head"],
        "topic_count": len(plan["map"]),
        "source_anchor_count": 0,
        "ids_unique": True,
        "controls_verified": len(plan["control_actions"]),
        "api_reachability": api_reachability,
        "unexpected_diff_paths": [],
        "missing_diff_paths": [],
        "verified_diff_paths": sorted(actual_diff),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.migration-tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _checkpoint_binding(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": f"{PLAN_KIND}-checkpoint",
        "plan_hash": plan["plan_hash"],
        "source_head": plan["source_head"],
        "repo_root": plan["repo_root"],
        "legacy_root": plan["legacy_root"],
    }


def _checkpoint_path(
    repo_root: Path,
    maintenance_sentinel: str | os.PathLike[str],
    checkpoint_path: str | os.PathLike[str] | None,
) -> Path:
    if checkpoint_path is None:
        checkpoint = Path(
            f"{Path(maintenance_sentinel).expanduser().resolve()}.checkpoint.json"
        )
    else:
        checkpoint = Path(checkpoint_path).expanduser().resolve()
    try:
        checkpoint.relative_to(repo_root)
    except ValueError:
        return checkpoint
    raise MigrationError(
        "INVALID_CHECKPOINT_PATH",
        "migration checkpoint must live outside the Git repository",
    )


def _load_checkpoint(
    checkpoint: Path,
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    if not checkpoint.exists():
        return None
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_MIGRATION_CHECKPOINT",
            f"checkpoint is unreadable: {checkpoint}",
        ) from exc
    expected = _checkpoint_binding(plan)
    if not isinstance(payload, dict) or any(
        payload.get(key) != value
        for key, value in expected.items()
    ):
        raise MigrationError(
            "INVALID_MIGRATION_CHECKPOINT",
            "checkpoint is not bound to this exact plan, HEAD and repository",
        )
    if payload.get("status") not in {"applying", "verified"}:
        raise MigrationError(
            "INVALID_MIGRATION_CHECKPOINT",
            "checkpoint status is invalid",
        )
    for key in ("completed_topics", "completed_controls"):
        if not isinstance(payload.get(key), list) or any(
            not isinstance(value, str)
            for value in payload[key]
        ):
            raise MigrationError(
                "INVALID_MIGRATION_CHECKPOINT",
                f"checkpoint {key} must be a string list",
            )
    return payload


def _save_checkpoint(checkpoint: Path, payload: dict[str, Any]) -> None:
    _atomic_write(checkpoint, canonical_json(payload))


def _validate_resume_gates(
    plan: dict[str, Any],
    repo_root: Path,
    legacy_root: Path,
    *,
    expected_head: str,
    expected_plan_hash: str,
    maintenance_sentinel: str | os.PathLike[str],
) -> None:
    _validate_plan(plan)
    if str(repo_root) != plan["repo_root"] or str(legacy_root) != plan["legacy_root"]:
        raise MigrationError(
            "PLAN_ROOT_MISMATCH",
            "explicit roots do not match the plan",
        )
    if expected_plan_hash != plan["plan_hash"]:
        raise MigrationError(
            "PLAN_HASH_CAS_MISMATCH",
            "expected plan hash does not match the plan",
        )
    if expected_head != plan["source_head"] or _git_head(repo_root) != expected_head:
        raise MigrationError(
            "HEAD_CAS_MISMATCH",
            "HEAD changed while migration was in progress",
        )
    sentinel = Path(maintenance_sentinel).expanduser().resolve()
    try:
        sentinel_payload = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_MAINTENANCE_SENTINEL",
            "maintenance sentinel disappeared or changed during resume",
        ) from exc
    if sentinel_payload != maintenance_sentinel_payload(plan):
        raise MigrationError(
            "INVALID_MAINTENANCE_SENTINEL",
            "maintenance sentinel is not bound to this plan and HEAD",
        )
    allowed_partial_sources: set[str] = set()
    for source, (expected_hash, expected_size) in (
        _planned_untracked_source_records(plan).items()
    ):
        source_path = repo_root / source
        if not source_path.exists() and not source_path.is_symlink():
            continue
        if source_path.is_symlink() or not source_path.is_file():
            raise MigrationError(
                "IGNORED_SOURCE_CHANGED",
                f"planned ignored source is no longer a regular file: {source}",
            )
        actual_hash, actual_size = _hash_file(source_path)
        if actual_hash != expected_hash or actual_size != expected_size:
            raise MigrationError(
                "IGNORED_SOURCE_CHANGED",
                f"planned ignored source changed during resume: {source}",
            )
        allowed_partial_sources.add(source)
    unexpected = sorted(
        _git_diff_paths(repo_root)
        - set(plan["expected_diff_paths"])
        - allowed_partial_sources
    )
    if unexpected:
        raise MigrationError(
            "UNEXPECTED_RESUME_DIFF",
            "resume found changes outside the frozen migration plan",
            details={"unexpected": unexpected},
        )


def _hash_file(path: Path) -> tuple[str, int]:
    content = path.read_bytes()
    return sha256_bytes(content), len(content)


def _move_planned_file(
    repo: Path,
    source: Path,
    destination: Path,
    *,
    expected_hash: str,
    expected_size: int,
    git_tracked: bool,
    destination_hash: str | None = None,
    destination_size: int | None = None,
) -> None:
    source_exists = source.is_file() and not source.is_symlink()
    destination_exists = destination.is_file() and not destination.is_symlink()
    if source_exists and destination_exists:
        raise MigrationError(
            "PARTIAL_MOVE_COLLISION",
            f"both source and destination exist: {source} -> {destination}",
        )
    if not source_exists and not destination_exists:
        raise MigrationError(
            "PARTIAL_MOVE_MISSING",
            f"neither source nor destination exists: {source} -> {destination}",
        )
    if destination_exists:
        actual_hash, actual_size = _hash_file(destination)
        allowed = {
            (expected_hash, expected_size),
            (
                destination_hash or expected_hash,
                destination_size
                if destination_size is not None
                else expected_size,
            ),
        }
        if (actual_hash, actual_size) not in allowed:
            raise MigrationError(
                "PARTIAL_MOVE_CONTENT_MISMATCH",
                f"resumed destination differs from plan: {destination}",
            )
        _git(repo, "add", "-f", "--", destination.relative_to(repo).as_posix())
        return

    actual_hash, actual_size = _hash_file(source)
    if actual_hash != expected_hash or actual_size != expected_size:
        raise MigrationError(
            "SOURCE_HASH_MISMATCH",
            f"source content changed before move: {source}",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if git_tracked:
        _git(
            repo,
            "mv",
            "--",
            source.relative_to(repo).as_posix(),
            destination.relative_to(repo).as_posix(),
        )
    else:
        shutil.move(str(source), str(destination))
        _git(repo, "add", "-f", "--", destination.relative_to(repo).as_posix())


def _remove_empty_tree(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def _apply_topic(repo: Path, item: dict[str, Any]) -> None:
    source_root = repo / item["old_repo_path"]
    destination_root = repo / item["new_repo_path"]
    if source_root.exists() and (
        source_root.is_symlink() or not source_root.is_dir()
    ):
        raise MigrationError(
            "SOURCE_ANCHOR_MISSING",
            f"planned source is not a directory: {source_root}",
        )
    if destination_root.exists() and (
        destination_root.is_symlink() or not destination_root.is_dir()
    ):
        raise MigrationError(
            "DESTINATION_OVERLAP",
            f"planned destination is not a directory: {destination_root}",
        )
    destination_root.mkdir(parents=True, exist_ok=True)
    for record in item["content_hashes"]:
        if record["before_sha256"] is None:
            continue
        source = source_root / record["source_relative_path"]
        destination = destination_root / record["relative_path"]
        _move_planned_file(
            repo,
            source,
            destination,
            expected_hash=record["before_sha256"],
            expected_size=record["size_before"],
            git_tracked=record["git_tracked"],
            destination_hash=record["after_sha256"],
            destination_size=record["size_after"],
        )

    brief_content = base64.b64decode(item["brief_after_b64"])
    _atomic_write(destination_root / "_brief.md", brief_content)
    _git(
        repo,
        "add",
        "-f",
        "--",
        f"{item['new_repo_path']}/_brief.md",
    )
    _remove_empty_tree(source_root)


def _apply_control_action(repo: Path, action: dict[str, Any]) -> None:
    destination = repo / action["destination_repo_path"]
    if action["kind"] == "move":
        _move_planned_file(
            repo,
            repo / action["source_repo_path"],
            destination,
            expected_hash=action["source_sha256"],
            expected_size=action["size"],
            git_tracked=action["git_tracked"],
        )
        return
    if action["kind"] == "copy":
        source = repo / action["source_repo_path"]
        actual_hash, actual_size = _hash_file(source)
        if (
            actual_hash != action["source_sha256"]
            or actual_size != action["size"]
        ):
            raise MigrationError(
                "CONTROL_CHANGED",
                f"control source changed before archive copy: {source}",
            )
        if destination.exists():
            destination_hash, destination_size = _hash_file(destination)
            if (
                destination_hash != action["source_sha256"]
                or destination_size != action["size"]
            ):
                raise MigrationError(
                    "CONTROL_DESTINATION_OVERLAP",
                    f"archive copy destination differs: {destination}",
                )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        _git(repo, "add", "-f", "--", action["destination_repo_path"])
        return

    content = base64.b64decode(action["content_b64"])
    if destination.exists() and (
        destination.is_symlink() or not destination.is_file()
    ):
        raise MigrationError(
            "CONTROL_DESTINATION_OVERLAP",
            f"generated control destination is not a file: {destination}",
        )
    _atomic_write(destination, content)
    _git(repo, "add", "-f", "--", action["destination_repo_path"])


def _verify_completed_control(repo: Path, action: dict[str, Any]) -> None:
    destination = repo / action["destination_repo_path"]
    if destination.is_symlink() or not destination.is_file():
        raise MigrationError(
            "INVALID_MIGRATION_CHECKPOINT",
            f"completed control destination is missing: {destination}",
        )
    expected_hash = (
        action["source_sha256"]
        if action["kind"] in {"copy", "move"}
        else action["after_sha256"]
    )
    if sha256_bytes(destination.read_bytes()) != expected_hash:
        raise MigrationError(
            "INVALID_MIGRATION_CHECKPOINT",
            f"completed control destination changed: {destination}",
        )
    if action["kind"] == "move":
        source = repo / action["source_repo_path"]
        if source.exists() or source.is_symlink():
            raise MigrationError(
                "INVALID_MIGRATION_CHECKPOINT",
                f"completed control source reappeared: {source}",
            )


def _cleanup_legacy_containers(repo: Path, legacy: Path) -> None:
    _remove_empty_tree(legacy)
    current = legacy.parent
    while current != repo:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def apply_plan(
    plan: dict[str, Any],
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
    *,
    expected_head: str,
    expected_plan_hash: str,
    maintenance_sentinel: str | os.PathLike[str],
    checkpoint_path: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply and verify while holding the governed repository mutation lock."""

    from katana_kernel.gitops import (  # noqa: PLC0415 - standalone script
        repository_mutation_lock,
    )

    repo, _, _ = _validate_roots(
        repo_root,
        legacy_root,
        require_legacy=False,
    )
    with repository_mutation_lock(str(repo)):
        return _apply_plan_locked(
            plan,
            repo,
            legacy_root,
            expected_head=expected_head,
            expected_plan_hash=expected_plan_hash,
            maintenance_sentinel=maintenance_sentinel,
            checkpoint_path=checkpoint_path,
            dry_run=dry_run,
        )


def _apply_plan_locked(
    plan: dict[str, Any],
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
    *,
    expected_head: str,
    expected_plan_hash: str,
    maintenance_sentinel: str | os.PathLike[str],
    checkpoint_path: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a frozen plan after all gates; caller owns the mutation lock."""

    repo, legacy, _ = _validate_roots(
        repo_root,
        legacy_root,
        require_legacy=False,
    )
    checkpoint = _checkpoint_path(
        repo,
        maintenance_sentinel,
        checkpoint_path,
    )
    checkpoint_payload = _load_checkpoint(checkpoint, plan)
    if checkpoint_payload is None:
        if not legacy.is_dir():
            raise MigrationError(
                "LEGACY_ROOT_NOT_FOUND",
                f"not a directory: {legacy}",
            )
        _validate_apply_gates(
            plan,
            repo,
            legacy,
            expected_head=expected_head,
            expected_plan_hash=expected_plan_hash,
            maintenance_sentinel=maintenance_sentinel,
        )
    else:
        _validate_resume_gates(
            plan,
            repo,
            legacy,
            expected_head=expected_head,
            expected_plan_hash=expected_plan_hash,
            maintenance_sentinel=maintenance_sentinel,
        )

    moves = [
        {
            "source": item["old_repo_path"],
            "destination": item["new_repo_path"],
        }
        for item in plan["map"]
    ]
    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "plan_hash": plan["plan_hash"],
            "source_head": plan["source_head"],
            "checkpoint_path": str(checkpoint),
            "moves": moves,
        }

    if checkpoint_payload is None:
        checkpoint_payload = {
            **_checkpoint_binding(plan),
            "status": "applying",
            "completed_topics": [],
            "completed_controls": [],
        }
        _save_checkpoint(checkpoint, checkpoint_payload)
    elif checkpoint_payload["status"] == "verified":
        verification = verify_plan(plan, repo, legacy)
        return {
            "applied": True,
            "dry_run": False,
            "resumed": True,
            "plan_hash": plan["plan_hash"],
            "source_head": plan["source_head"],
            "checkpoint_path": str(checkpoint),
            "moves": moves,
            "verification": verification,
        }

    for item in plan["map"]:
        _apply_topic(repo, item)
        if item["new_id"] not in checkpoint_payload["completed_topics"]:
            checkpoint_payload["completed_topics"].append(item["new_id"])
            _save_checkpoint(checkpoint, checkpoint_payload)

    for action in plan["control_actions"]:
        if action["action_id"] in checkpoint_payload["completed_controls"]:
            _verify_completed_control(repo, action)
            continue
        _apply_control_action(repo, action)
        if action["action_id"] not in checkpoint_payload["completed_controls"]:
            checkpoint_payload["completed_controls"].append(action["action_id"])
            _save_checkpoint(checkpoint, checkpoint_payload)

    _cleanup_legacy_containers(repo, legacy)

    verification = verify_plan(plan, repo, legacy)
    checkpoint_payload["status"] = "verified"
    _save_checkpoint(checkpoint, checkpoint_payload)
    return {
        "applied": True,
        "dry_run": False,
        "plan_hash": plan["plan_hash"],
        "source_head": plan["source_head"],
        "checkpoint_path": str(checkpoint),
        "moves": moves,
        "verification": verification,
    }


def _load_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "INVALID_JSON_INPUT",
            f"cannot read JSON input: {path}",
        ) from exc
    if not isinstance(value, dict):
        raise MigrationError(
            "INVALID_JSON_INPUT",
            f"JSON input must be an object: {path}",
        )
    return value


def _emit(value: dict[str, Any], output: str | None) -> None:
    payload = canonical_json(value)
    if output:
        Path(output).write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--legacy-root", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline fail-closed Work Folder flat migration",
    )
    subparsers = parser.add_subparsers(dest="phase", required=True)

    inventory_parser = subparsers.add_parser("inventory")
    _add_roots(inventory_parser)
    inventory_parser.add_argument("--output")

    plan_parser = subparsers.add_parser("plan")
    _add_roots(plan_parser)
    plan_parser.add_argument("--inventory", required=True)
    plan_parser.add_argument("--repairs")
    plan_parser.add_argument("--output")

    sentinel_parser = subparsers.add_parser("sentinel")
    _add_roots(sentinel_parser)
    sentinel_parser.add_argument("--plan", required=True)
    sentinel_parser.add_argument("--expected-plan-hash", required=True)
    sentinel_parser.add_argument("--output", required=True)

    apply_parser = subparsers.add_parser("apply")
    _add_roots(apply_parser)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--expected-head", required=True)
    apply_parser.add_argument("--expected-plan-hash", required=True)
    apply_parser.add_argument("--maintenance-sentinel", required=True)
    apply_parser.add_argument(
        "--checkpoint",
        help=(
            "external durable resume checkpoint; defaults beside the "
            "maintenance sentinel"
        ),
    )
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument("--output")

    verify_parser = subparsers.add_parser("verify")
    _add_roots(verify_parser)
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--output")

    return parser


def _validate_cli_roots(
    value: dict[str, Any],
    repo_root: str,
    legacy_root: str,
) -> None:
    repo, legacy, _ = _validate_roots(repo_root, legacy_root)
    if value.get("repo_root") != str(repo) or value.get("legacy_root") != str(legacy):
        raise MigrationError(
            "PLAN_ROOT_MISMATCH",
            "explicit roots do not match the input artifact",
        )


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.phase == "inventory":
            result = build_inventory(args.repo_root, args.legacy_root)
            _emit(result, args.output)
            return 0 if result["ok"] else 1
        if args.phase == "plan":
            inventory = _load_json(args.inventory)
            _validate_cli_roots(inventory, args.repo_root, args.legacy_root)
            repairs = _load_json(args.repairs) if args.repairs else None
            result = build_plan(inventory, repairs=repairs)
            _emit(result, args.output)
            return 0
        if args.phase == "sentinel":
            plan = _load_json(args.plan)
            _validate_cli_roots(plan, args.repo_root, args.legacy_root)
            _validate_plan(plan)
            _emit(
                maintenance_sentinel_payload(
                    plan,
                    expected_plan_hash=args.expected_plan_hash,
                ),
                args.output,
            )
            return 0
        if args.phase == "apply":
            plan = _load_json(args.plan)
            result = apply_plan(
                plan,
                args.repo_root,
                args.legacy_root,
                expected_head=args.expected_head,
                expected_plan_hash=args.expected_plan_hash,
                maintenance_sentinel=args.maintenance_sentinel,
                checkpoint_path=args.checkpoint,
                dry_run=args.dry_run,
            )
            _emit(result, args.output)
            return 0
        if args.phase == "verify":
            plan = _load_json(args.plan)
            result = verify_plan(
                plan,
                args.repo_root,
                args.legacy_root,
            )
            _emit(result, args.output)
            return 0
    except MigrationError as exc:
        sys.stderr.buffer.write(canonical_json(exc.as_dict()))
        return 1
    raise AssertionError(f"unknown phase: {args.phase}")


if __name__ == "__main__":
    raise SystemExit(main())
