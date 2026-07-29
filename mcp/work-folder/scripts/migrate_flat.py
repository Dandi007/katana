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
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


SCHEMA_VERSION = 1
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
    ".katana",
    "MIGRATION_BASE.json",
}
LEGACY_ROOT_CONTROL_NAMES = {"INDEX.md", "AGENTS.md", "CLAUDE.md", ".katana"}

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


def _validate_roots(
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
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
    if not legacy.is_dir():
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


def _control_hashes(control: Path, repo_root: Path) -> list[dict[str, Any]]:
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
        return [{
            "relative_path": control.relative_to(repo_root).as_posix(),
            "kind": "regular_file",
            "sha256": sha256_bytes(content),
            "size": len(content),
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
) -> dict[str, Any]:
    return {
        "repo_relative_path": control.relative_to(repo_root).as_posix(),
        "classification": classification,
        "entries": _control_hashes(control, repo_root),
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
        if item_locator not in tracked:
            errors.append(_error(
                "UNTRACKED_TOPIC_FILE",
                item_locator,
                "every topic file must be tracked before planning",
            ))
        content = item.read_bytes()
        files.append({
            "relative_path": item.relative_to(topic).as_posix(),
            "repo_relative_path": item_locator,
            "sha256": sha256_bytes(content),
            "size": len(content),
        })

    if not files:
        errors.append(_error(
            "EMPTY_TOPIC",
            locator,
            "topic contains no regular tracked files",
        ))

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

    allowed_root_component = legacy_relative.parts[0]
    for item in _sorted_children(repo):
        if item.name == ".git":
            controls.append(_control_record(item, repo, "git-metadata"))
            continue
        if item.name == allowed_root_component:
            continue
        if item.name in REPO_ROOT_CONTROL_NAMES:
            controls.append(_control_record(item, repo, "root-control"))
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

    for year_entry in _sorted_children(legacy):
        if year_entry.name in LEGACY_ROOT_CONTROL_NAMES:
            controls.append(
                _control_record(year_entry, repo, "legacy-root-control")
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
                        legacy,
                        topic_entry,
                        tracked,
                        errors,
                    ))

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

    inventory: dict[str, Any] = {
        "kind": f"{PLAN_KIND}-inventory",
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo),
        "legacy_root": str(legacy),
        "legacy_root_relative": legacy_relative.as_posix(),
        "source_head": _git_head(repo),
        "topics": topics,
        "controls": controls,
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


def build_plan(
    inventory: dict[str, Any],
    *,
    repairs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, content-addressed flat migration plan."""

    if inventory.get("kind") != f"{PLAN_KIND}-inventory":
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
        if topic["canonical_id_unique"]
    }
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
        for file_record in topic["files"]:
            relative_path = file_record["relative_path"]
            before_hash = file_record["sha256"]
            before_size = file_record["size"]
            if relative_path == "_brief.md":
                saw_brief = True
                after_hash = sha256_bytes(brief_after)
                after_size = len(brief_after)
            else:
                after_hash = before_hash
                after_size = before_size
            hashes.append({
                "relative_path": relative_path,
                "before_sha256": before_hash,
                "after_sha256": after_hash,
                "size_before": before_size,
                "size_after": after_size,
            })
        if not saw_brief:
            hashes.append({
                "relative_path": "_brief.md",
                "before_sha256": None,
                "after_sha256": sha256_bytes(brief_after),
                "size_before": None,
                "size_after": len(brief_after),
            })
        hashes.sort(key=lambda item: item["relative_path"].encode("utf-8"))

        source_repo_path = topic["source_repo_path"]
        for hash_record in hashes:
            relative_path = hash_record["relative_path"]
            if hash_record["before_sha256"] is not None:
                expected_diff_paths.add(
                    f"{source_repo_path}/{relative_path}"
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

    plan: dict[str, Any] = {
        "kind": PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "repo_root": inventory["repo_root"],
        "legacy_root": inventory["legacy_root"],
        "legacy_root_relative": inventory["legacy_root_relative"],
        "source_head": inventory["source_head"],
        "inventory_hash": inventory["inventory_hash"],
        "controls": inventory["controls"],
        "map": mapping,
        "expected_diff_paths": sorted(
            expected_diff_paths,
            key=lambda value: value.encode("utf-8"),
        ),
    }
    plan["plan_hash"] = _hash_without_key(plan, "plan_hash")
    _validate_plan(plan)
    return plan


def maintenance_sentinel_payload(plan: dict[str, Any]) -> dict[str, Any]:
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
    expected_old_path = f"{legacy_relative}/{locator}"
    if old_repo_path != expected_old_path:
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
    expected_diff: set[str] = set()
    for record in hashes:
        if not isinstance(record, dict):
            _invalid_plan("content hash entries must be objects")
        relative_path = record.get("relative_path")
        if not _is_safe_relative_path(relative_path):
            _invalid_plan("content hash contains an unsafe relative path")
        if relative_path in by_path:
            _invalid_plan("content hash paths must be unique per topic")

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
        if before_hash is not None:
            expected_diff.add(f"{old_repo_path}/{relative_path}")
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
    return expected_diff


def _validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("kind") != PLAN_KIND:
        raise MigrationError("INVALID_PLAN", "plan kind is not recognized")
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
    mapping = plan.get("map")
    if not isinstance(mapping, list) or not mapping:
        raise MigrationError("INVALID_PLAN", "plan map is empty")
    expected_diff: set[str] = set()
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
            record["relative_path"]: record
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
    for year_entry in _sorted_children(legacy_root):
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
                            topic_entry.relative_to(legacy_root).as_posix()
                        )
    return sorted(anchors, key=lambda value: value.encode("utf-8"))


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
    return {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }


def _verify_planned_controls(
    plan: dict[str, Any],
    repo_root: Path,
) -> None:
    for expected in plan["controls"]:
        relative_path = expected["repo_relative_path"]
        classification = expected["classification"]
        control = repo_root / relative_path
        if classification == "git-metadata":
            if not control.exists():
                raise MigrationError(
                    "CONTROL_CHANGED",
                    "Git metadata disappeared during migration",
                )
            continue
        if classification in {"legacy-container", "legacy-root"}:
            if control.is_symlink() or not control.is_dir():
                raise MigrationError(
                    "CONTROL_CHANGED",
                    f"migration container changed: {relative_path}",
                )
            continue
        if not control.exists() and not control.is_symlink():
            raise MigrationError(
                "CONTROL_CHANGED",
                f"inventoried control disappeared: {relative_path}",
            )
        actual = _control_record(
            control,
            repo_root,
            classification,
        )
        if actual != expected:
            raise MigrationError(
                "CONTROL_CHANGED",
                f"inventoried control changed: {relative_path}",
            )


def verify_plan(
    plan: dict[str, Any],
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Verify the migrated working tree against a frozen plan."""

    _validate_plan(plan)
    repo, legacy, _ = _validate_roots(repo_root, legacy_root)
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

    return {
        "ok": True,
        "plan_hash": plan["plan_hash"],
        "source_head": plan["source_head"],
        "topic_count": len(plan["map"]),
        "source_anchor_count": 0,
        "ids_unique": True,
        "controls_verified": len(plan["controls"]),
        "unexpected_diff_paths": [],
        "missing_diff_paths": [],
        "verified_diff_paths": sorted(actual_diff),
    }


def apply_plan(
    plan: dict[str, Any],
    repo_root: str | os.PathLike[str],
    legacy_root: str | os.PathLike[str],
    *,
    expected_head: str,
    expected_plan_hash: str,
    maintenance_sentinel: str | os.PathLike[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a frozen plan after all Git, HEAD, plan, content and sentinel gates."""

    repo, legacy, _ = _validate_roots(repo_root, legacy_root)
    _validate_apply_gates(
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
            "moves": moves,
        }

    for item in plan["map"]:
        source = item["old_repo_path"]
        destination = item["new_repo_path"]
        _git(repo, "mv", "--", source, destination)
        brief = repo / destination / "_brief.md"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_bytes(base64.b64decode(item["brief_after_b64"]))
        _git(repo, "add", "--", f"{destination}/_brief.md")

    verification = verify_plan(plan, repo, legacy)
    return {
        "applied": True,
        "dry_run": False,
        "plan_hash": plan["plan_hash"],
        "source_head": plan["source_head"],
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

    apply_parser = subparsers.add_parser("apply")
    _add_roots(apply_parser)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--expected-head", required=True)
    apply_parser.add_argument("--expected-plan-hash", required=True)
    apply_parser.add_argument("--maintenance-sentinel", required=True)
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
        if args.phase == "apply":
            plan = _load_json(args.plan)
            result = apply_plan(
                plan,
                args.repo_root,
                args.legacy_root,
                expected_head=args.expected_head,
                expected_plan_hash=args.expected_plan_hash,
                maintenance_sentinel=args.maintenance_sentinel,
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
