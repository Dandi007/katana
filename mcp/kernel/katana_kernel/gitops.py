"""gitops: idempotent git commit + CAS guard for governed repos."""

from __future__ import annotations

import os
import subprocess


class CASRejectionError(Exception):
    """Raised when expected_base_sha does not match current HEAD."""


def _run(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True, text=True, timeout=30,
    )


def head_sha(repo_root: str) -> str:
    try:
        r = _run(repo_root, "rev-parse", "HEAD")
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


def is_working_tree_clean(repo_root: str) -> bool:
    try:
        r = _run(repo_root, "status", "--porcelain")
        return r.returncode == 0 and r.stdout.strip() == ""
    except (subprocess.SubprocessError, OSError):
        return False


def cas_guard(repo_root: str, expected_base_sha: str | None) -> None:
    if expected_base_sha is None:
        return
    current = head_sha(repo_root)
    if not current:
        raise CASRejectionError("no HEAD in repo; cannot verify CAS")
    if current != expected_base_sha:
        raise CASRejectionError(
            f"CAS mismatch: expected {expected_base_sha[:8]}..., got {current[:8]}..."
        )


def _restore_tree(repo_root: str) -> None:
    subprocess.run(
        ["git", "-C", repo_root, "reset", "--hard", "HEAD"],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", repo_root, "clean", "-fd"],
        capture_output=True, timeout=30,
    )


def git_commit(
    repo_root: str,
    message: str,
    paths: list[str],
) -> dict:
    try:
        add = _run(repo_root, "add", "--", *paths)
        if add.returncode != 0:
            return {"committed": False, "detail": add.stderr.strip()}
        diff = _run(repo_root, "diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return {"committed": False, "detail": "nothing to commit"}
        c = _run(repo_root, "commit", "-m", message)
        if c.returncode != 0:
            return {"committed": False, "detail": c.stderr.strip() or c.stdout.strip()}
        sha = head_sha(repo_root)
        return {
            "committed": True,
            "detail": sha if sha else c.stdout.strip().splitlines()[0] if c.stdout.strip() else "committed",
        }
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}


def amend_commit(
    repo_root: str,
    paths: list[str],
) -> dict:
    try:
        add = _run(repo_root, "add", "--", *paths)
        if add.returncode != 0:
            return {"committed": False, "detail": add.stderr.strip()}
        c = _run(repo_root, "commit", "--amend", "--no-edit")
        if c.returncode != 0:
            return {"committed": False, "detail": c.stderr.strip() or c.stdout.strip()}
        sha = head_sha(repo_root)
        return {
            "committed": True,
            "detail": sha if sha else "committed",
        }
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}