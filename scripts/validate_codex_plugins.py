#!/usr/bin/env python3
"""Validate Codex plugin wrappers and marketplace metadata."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


def fail(message: str) -> None:
    print(f"G0-FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def plugin_dirs(kind: str) -> set[str]:
    return {path.parent.parent.name for path in ROOT.glob(f"plugins/*/.{kind}-plugin/plugin.json")}


def validate_manifest(plugin_dir: Path) -> None:
    manifest_path = plugin_dir / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path)
    for key in ("name", "version", "description", "author", "skills", "interface"):
        if not manifest.get(key):
            fail(f"{manifest_path.relative_to(ROOT)} missing non-empty {key!r}")
    if not isinstance(manifest["author"], dict):
        fail(f"{manifest_path.relative_to(ROOT)} author must be an object")
    if not isinstance(manifest["interface"], dict):
        fail(f"{manifest_path.relative_to(ROOT)} interface must be an object")
    if not manifest["interface"].get("defaultPrompt"):
        fail(f"{manifest_path.relative_to(ROOT)} interface.defaultPrompt is required")
    skills_dir = plugin_dir / manifest["skills"]
    if not skills_dir.is_dir():
        fail(f"{manifest_path.relative_to(ROOT)} skills path does not exist: {manifest['skills']}")
    if not list(skills_dir.glob("*/SKILL.md")):
        fail(f"{skills_dir.relative_to(ROOT)} contains no */SKILL.md files")


def main() -> None:
    marketplace = load_json(MARKETPLACE)
    if marketplace.get("name") != "katana":
        fail("Codex marketplace name must be katana")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or not entries:
        fail("Codex marketplace plugins must be a non-empty list")

    mkt = {entry.get("name") for entry in entries}
    disk = plugin_dirs("codex")
    claude = plugin_dirs("claude")
    if mkt != disk:
        fail(f"Codex marketplace {sorted(mkt)} != disk {sorted(disk)}")
    if disk != claude:
        fail(f"Codex plugin set {sorted(disk)} != Claude plugin set {sorted(claude)}")

    marketplace_root = MARKETPLACE.parents[2]
    for entry in entries:
        name = entry.get("name")
        source = entry.get("source") or {}
        policy = entry.get("policy") or {}
        if source.get("source") != "local":
            fail(f"{name}: source.source must be local")
        if not source.get("path", "").startswith("./"):
            fail(f"{name}: source.path must be ./ relative")
        if policy.get("installation") != "AVAILABLE":
            fail(f"{name}: policy.installation must be AVAILABLE")
        if policy.get("authentication") != "ON_INSTALL":
            fail(f"{name}: policy.authentication must be ON_INSTALL")
        if not entry.get("category"):
            fail(f"{name}: category is required")
        plugin_dir = (marketplace_root / source["path"]).resolve()
        if not plugin_dir.is_dir():
            fail(f"{name}: plugin dir missing: {source['path']}")
        validate_manifest(plugin_dir)

    print(f"Codex plugin metadata: OK ({len(entries)} plugins)")


if __name__ == "__main__":
    main()
