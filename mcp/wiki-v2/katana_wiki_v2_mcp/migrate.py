"""katana-wiki-v2-migrate — migration CLI from v1 wiki data repo to v2 data repo.

Usage:
    katana-wiki-v2-migrate --source <v1-wiki-repo> --dest <v2-repo-dir> [--dry-run]

Reads <source>/Zettelkasten/**/*.md (excluding .* dirs and _quarantine/),
flattens to <dest>/pages/<title>.md, normalizes wikilinks, detects conflicts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


def _parse_page(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    end = rest.find("\n---\n")
    if end == -1:
        return {}, text
    yaml_text = rest[:end]
    body = rest[end + 5:]
    fm = yaml.safe_load(yaml_text) or {}
    if not isinstance(fm, dict):
        return {}, text
    return fm, body


def _render_page(fm: dict, body: str) -> str:
    yaml_text = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_text}---\n{body}"


def _make_id(seed: str, existing_ids: set[str]) -> str:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    candidate = "w-" + h[:6]
    counter = 0
    while candidate in existing_ids:
        counter += 1
        h = hashlib.sha256(f"{seed}\x00{counter}".encode("utf-8")).hexdigest()
        candidate = "w-" + h[:6]
    return candidate


def _rewrite_wikilinks(body: str) -> tuple[str, int]:
    import re
    wikilink_re = re.compile(r"\[\[([^\]]+)\]\]")
    count = 0

    def _replace(m: re.Match) -> str:
        nonlocal count
        inner = m.group(1)
        if "|" in inner:
            target, alias = inner.split("|", 1)
            target = target.strip()
            alias = alias.strip()
            new_target = _normalize_link_target(target)
            if new_target != target:
                count += 1
                return f"[[{new_target}|{alias}]]"
            return m.group(0)
        else:
            target = inner.strip()
            new_target = _normalize_link_target(target)
            if new_target != target:
                count += 1
                return f"[[{new_target}]]"
            return m.group(0)

    return wikilink_re.sub(_replace, body), count


def _normalize_link_target(target: str) -> str:
    if "/" in target:
        return target.rsplit("/", 1)[-1]
    return target


def _is_excluded(parts: tuple[str, ...]) -> bool:
    for part in parts:
        if part.startswith(".") or part == "_quarantine":
            return True
    return False


def _scan_source(source: str) -> list[dict]:
    source_root = Path(source)
    zettelkasten = source_root / "Zettelkasten"
    if not zettelkasten.is_dir():
        print(f"ERROR: Zettelkasten/ not found in source: {source}", file=sys.stderr)
        sys.exit(1)

    pages: list[dict] = []
    for md_file in sorted(zettelkasten.rglob("*.md")):
        rel = md_file.relative_to(zettelkasten)
        parts = tuple(rel.parts)
        if _is_excluded(parts):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            fm, body = _parse_page(text)
            title = md_file.stem
            pages.append({
                "source_path": str(rel),
                "title": title,
                "frontmatter": fm,
                "body": body,
            })
        except Exception as e:
            print(f"WARNING: skip unreadable file {md_file}: {e}", file=sys.stderr)
    return pages


def migrate(source: str, dest: str, dry_run: bool = False) -> dict:
    pages = _scan_source(source)

    conflicts: list[dict] = []
    seen_titles: dict[str, list[dict]] = {}
    for page in pages:
        seen_titles.setdefault(page["title"], []).append(page)

    for title, entries in seen_titles.items():
        if len(entries) > 1:
            conflicts.append({
                "title": title,
                "sources": [e["source_path"] for e in entries],
            })

    if conflicts:
        conflict_report = {
            "conflicts": conflicts,
            "total_pages": len(pages),
            "conflict_count": len(conflicts),
        }
        conflict_path = Path(dest) / "migration-conflicts.json"
        if dry_run:
            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps(conflict_report, ensure_ascii=False, indent=2))
            print(json.dumps(conflict_report, ensure_ascii=False, indent=2))
        else:
            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps(conflict_report, ensure_ascii=False, indent=2))
            print(json.dumps(conflict_report, ensure_ascii=False, indent=2))
        return {"success": False, "conflict_report": conflict_report, "pages_processed": len(pages)}

    link_rewrites = 0
    id_issued = 0
    existing_ids: set[str] = set()

    for page in pages:
        if page["frontmatter"].get("id"):
            pid = page["frontmatter"]["id"]
            if pid.startswith("w-") and len(pid) == 8:
                existing_ids.add(pid)

    output_pages: list[dict] = []
    for page in pages:
        fm = dict(page["frontmatter"])
        body = page["body"]

        if not fm.get("id") or not (fm["id"].startswith("w-") and len(fm["id"]) == 8):
            seed = f"{page['title']}:{body[:200]}"
            new_id = _make_id(seed, existing_ids)
            fm["id"] = new_id
            existing_ids.add(new_id)
            id_issued += 1

        new_body, rewrites = _rewrite_wikilinks(body)
        link_rewrites += rewrites
        body = new_body

        output_pages.append({
            "title": page["title"],
            "frontmatter": fm,
            "body": body,
            "original_path": page["source_path"],
        })

    if not dry_run:
        dest_root = Path(dest)
        dest_root.mkdir(parents=True, exist_ok=True)

        wiki_md = Path(source) / "WIKI.md"
        if wiki_md.is_file():
            import shutil
            shutil.copy2(wiki_md, dest_root / "WIKI.md")

        (dest_root / "log.md").write_text("", encoding="utf-8")

        gitignore = dest_root / ".gitignore"
        gitignore.write_text(".katana/index/\n")

        pages_dir = dest_root / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        for page in output_pages:
            page_path = pages_dir / f"{page['title']}.md"
            page_path.write_text(_render_page(page["frontmatter"], page["body"]), encoding="utf-8")

        report = {
            "pages_migrated": len(output_pages),
            "link_rewrites": link_rewrites,
            "id_issued": id_issued,
            "skipped": 0,
        }
        with open(dest_root / "migration-report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        subprocess.run(["git", "-C", str(dest_root), "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(dest_root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(dest_root), "commit", "-m", "migration: initial import from v1"], check=True, capture_output=True)

        return {"success": True, "report": report, "pages": len(output_pages)}

    return {"success": True, "pages": len(output_pages), "dry_run": True,
            "link_rewrites": link_rewrites, "id_issued": id_issued}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate v1 wiki data to v2 wiki data repo")
    parser.add_argument("--source", required=True, help="Path to v1 wiki data repo")
    parser.add_argument("--dest", required=True, help="Path to v2 wiki data repo directory")
    parser.add_argument("--dry-run", action="store_true", help="Report conflicts only, do not write")
    args = parser.parse_args()

    result = migrate(args.source, args.dest, dry_run=args.dry_run)
    if not result["success"]:
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()