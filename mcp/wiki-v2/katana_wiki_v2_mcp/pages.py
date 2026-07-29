"""Wiki page IO layer — v2: flat pages/ directory, stable IDs, YAML frontmatter.

Pure functions: parse_page, render_page.
IO functions: read_page, write_page, scan_pages.
Wikilink functions: extract_wikilinks, rewrite_wikilinks.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import yaml


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_TITLE_FORBIDDEN_RE = re.compile(r"[\n/]")


def parse_page(text: str) -> tuple[dict, str]:
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


def render_page(fm: dict, body: str) -> str:
    yaml_text = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_text}---\n{body}"


def read_page(path: str) -> tuple[dict, str]:
    text = Path(path).read_text(encoding="utf-8")
    return parse_page(text)


def write_page(path: str, fm: dict, body: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_page(fm, body), encoding="utf-8")


def make_id(*, existing_ids: set[str] | None = None, seed: str = "") -> str:
    if seed:
        h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        candidate = "w-" + h[:6]
    else:
        candidate = "w-" + hashlib.sha256(os.urandom(32)).hexdigest()[:6]
    if existing_ids and candidate in existing_ids:
        return make_id(existing_ids=existing_ids, seed=seed + "\x00")
    return candidate


def extract_wikilinks(body: str) -> list[str]:
    return [m.group(1) for m in _WIKILINK_RE.finditer(body)]


def extract_wikilinks_with_aliases(body: str) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    for m in _WIKILINK_RE.finditer(body):
        inner = m.group(1)
        if "|" in inner:
            target, alias = inner.split("|", 1)
            result.append((target.strip(), alias.strip()))
        else:
            result.append((inner.strip(), None))
    return result


def rewrite_wikilinks(body: str, old_title: str, new_title: str) -> str:
    def _replace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            target, alias = inner.split("|", 1)
            target = target.strip()
            alias = alias.strip()
            if target == old_title:
                return f"[[{new_title}|{alias}]]"
            return m.group(0)
        else:
            if inner.strip() == old_title:
                return f"[[{new_title}]]"
            return m.group(0)
    return _WIKILINK_RE.sub(_replace, body)


def remove_wikilinks_for_title(body: str, title: str) -> str:
    def _replace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            target, alias = inner.split("|", 1)
            target = target.strip()
            alias = alias.strip()
            if target == title:
                return alias
            return m.group(0)
        else:
            if inner.strip() == title:
                return title
            return m.group(0)
    return _WIKILINK_RE.sub(_replace, body)


def validate_title(title: str) -> str | None:
    t = title.strip()
    if t != title:
        return "title must not have leading or trailing whitespace"
    if not t:
        return "title must not be empty"
    if _TITLE_FORBIDDEN_RE.search(t):
        return "title must not contain '/' or newline"
    return None


def title_to_path(title: str) -> str:
    return f"pages/{title}.md"


def path_to_title(path: str) -> str | None:
    if not path.startswith("pages/") or not path.endswith(".md"):
        return None
    return path[len("pages/"):-len(".md")]


def scan_pages(data_root: str) -> list[dict]:
    pages_dir = Path(data_root) / "pages"
    if not pages_dir.is_dir():
        return []
    pages: list[dict] = []
    for p in sorted(pages_dir.iterdir()):
        if not p.is_file() or not p.suffix == ".md":
            continue
        try:
            text = p.read_text(encoding="utf-8")
            fm, body = parse_page(text)
            title = p.stem
            pages.append({
                "path": f"pages/{p.name}",
                "title": title,
                "frontmatter": fm,
                "body": body,
                "id": fm.get("id"),
            })
        except Exception as e:
            pages.append({
                "path": f"pages/{p.name}",
                "title": p.stem,
                "frontmatter": {},
                "body": "",
                "id": None,
                "_error": str(e),
            })
    return pages


def find_page_by_title(data_root: str, title: str) -> dict | None:
    path = title_to_path(title)
    full = Path(data_root) / path
    if not full.is_file():
        return None
    try:
        text = full.read_text(encoding="utf-8")
        fm, body = parse_page(text)
        return {
            "path": path,
            "title": title,
            "frontmatter": fm,
            "body": body,
            "id": fm.get("id"),
        }
    except Exception as e:
        return {
            "path": path,
            "title": title,
            "frontmatter": {},
            "body": "",
            "id": None,
            "_error": str(e),
        }


def find_page_by_id(data_root: str, page_id: str) -> dict | None:
    for page in scan_pages(data_root):
        if page["id"] == page_id:
            return page
    return None


def find_page_by_ref(data_root: str, ref: str) -> dict | None:
    if ref.startswith("w-"):
        return find_page_by_id(data_root, ref)
    return find_page_by_title(data_root, ref)


def compute_inlinks(data_root: str, title: str) -> list[str]:
    inlinks: list[str] = []
    for page in scan_pages(data_root):
        if page["title"] == title:
            continue
        links = extract_wikilinks(page["body"])
        for link in links:
            if "|" in link:
                target = link.split("|", 1)[0].strip()
            else:
                target = link.strip()
            if target == title:
                inlinks.append(page["title"])
                break
    return inlinks


def compute_outlinks(body: str) -> list[str]:
    result: list[str] = []
    for target, _ in extract_wikilinks_with_aliases(body):
        if target not in result:
            result.append(target)
    return result