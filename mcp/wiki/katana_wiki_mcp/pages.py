"""Wiki page IO layer.

Pure functions: parse_page, render_page.
IO functions: read_page, write_page, ensure_backlink, append_log, git_commit, archive_inbox.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def parse_page(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter + body. Returns ({}, original) if no frontmatter."""
    if not text.startswith("---\n"):
        return {}, text
    # Find closing ---
    rest = text[4:]  # after first "---\n"
    end = rest.find("\n---\n")
    if end == -1:
        return {}, text
    yaml_text = rest[:end]
    body = rest[end + 5:]  # skip "\n---\n"
    fm = yaml.safe_load(yaml_text) or {}
    return fm, body


def render_page(fm: dict, body: str) -> str:
    """Render frontmatter dict + body back to markdown string."""
    yaml_text = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_text}---\n{body}"


# ---------------------------------------------------------------------------
# IO functions
# ---------------------------------------------------------------------------

def read_page(path: str) -> tuple[dict, str]:
    """Read file and parse frontmatter + body."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_page(text)


def write_page(path: str, fm: dict, body: str) -> None:
    """Render frontmatter + body and write to file (auto-create parent dirs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_page(fm, body), encoding="utf-8")


def ensure_backlink(path: str, target_title: str) -> bool:
    """Append backlink to page body if not already present. Returns True if added."""
    fm, body = read_page(path)
    link = f"[[{target_title}]]"
    if link in body:
        return False
    new_body = body.rstrip("\n") + f"\n- 关联：{link}\n"
    write_page(path, fm, new_body)
    return True


def append_log(wiki_root: str, line: str) -> None:
    """Append a line to <wiki_root>/log.md (creates if missing)."""
    log_path = Path(wiki_root) / "log.md"
    entry = line if line.endswith("\n") else line + "\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)


def git_commit(wiki_root: str, message: str, paths: list[str]) -> str:
    """Stage paths and commit in wiki_root. Returns short SHA. Idempotent: no-op if nothing staged."""
    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", wiki_root, *args],
            check=True,
            capture_output=True,
            text=True,
        )

    _run("add", "--", *paths)

    # Check if there are staged changes
    diff_result = subprocess.run(
        ["git", "-C", wiki_root, "diff", "--cached", "--quiet"],
        capture_output=True,
        text=True,
    )

    # diff --cached --quiet: exit 0 = no changes, exit 1 = has changes
    if diff_result.returncode == 0:
        # No staged changes; return current HEAD short SHA without committing
        result = _run("rev-parse", "--short", "HEAD")
        return result.stdout.strip()

    # Has staged changes; commit
    _run("commit", "-m", message)
    result = _run("rev-parse", "--short", "HEAD")
    return result.stdout.strip()


def archive_inbox(inbox_path: str, raw_dir: str, wiki_root: str) -> str:
    """git mv inbox file to raw_dir. Returns new path relative to wiki_root."""
    raw = Path(raw_dir)
    src = Path(inbox_path)
    dest = raw / src.name

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", wiki_root, *args],
            check=True,
            capture_output=True,
            text=True,
        )

    # Create raw_dir and stage it if needed
    if not raw.exists():
        raw.mkdir(parents=True, exist_ok=True)
        # Create a .gitkeep so the dir can be staged
        gitkeep = raw / ".gitkeep"
        gitkeep.write_text("", encoding="utf-8")
        _run("add", str(gitkeep))

    _run("mv", str(src), str(dest))

    return str(dest.relative_to(wiki_root))
