"""Thin Git plumbing wrapper for a single canonical data repo.

Only mechanics live here: object writes, ref read, compare-and-swap ref update,
first-parent history walk. No domain semantics. The protected canonical ref CAS
update is the single linearization point (design §6.3, INV-4).
"""
from __future__ import annotations

import subprocess

from .errors import COMMIT_FAILED, KernelError


class GitRepo:
    def __init__(self, root: str, ref: str = "HEAD") -> None:
        self.root = root
        self.ref = ref

    def _run(self, *args: str, check: bool = True,
             input_text: str | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", self.root, *args],
            capture_output=True, text=True, timeout=60, input=input_text,
        )
        if check and proc.returncode != 0:
            raise KernelError(
                COMMIT_FAILED,
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}",
            )
        return proc

    # ── initialisation ────────────────────────────────────────────────
    def ensure_repo(self) -> None:
        inside = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        )
        if inside.returncode != 0:
            self._run("init", "-q")
            self._run("config", "user.email", "kernel@katana.local")
            self._run("config", "user.name", "Katana Kernel")

    # ── ref reads ─────────────────────────────────────────────────────
    def head(self) -> str | None:
        proc = self._run("rev-parse", "--verify", "-q", self.ref, check=False)
        sha = proc.stdout.strip()
        return sha or None

    def has_commits(self) -> bool:
        return self.head() is not None

    # ── working-tree publish (compare-and-swap on the canonical ref) ──
    def commit_worktree(self, message: str, *, expected_base: str | None,
                        paths: list[str] | None = None) -> str:
        """Stage, verify CAS base, and commit; return the new commit SHA.

        ``expected_base`` is the base the caller resolved against. If the
        canonical ref moved underneath us the commit is refused (the caller
        maps this to BASE_COMMIT_CONFLICT). Publish is the linearization point:
        before it fails there is zero client-visible effect.
        """
        current = self.head()
        if expected_base is not None and current is not None \
                and current != expected_base:
            raise KernelError(
                COMMIT_FAILED,
                f"canonical ref advanced (base {expected_base[:8]} != head {current[:8]})",
                current_commit=current,
            )
        if paths:
            self._run("add", "-A", "--", *paths)
        else:
            self._run("add", "-A")
        # Nothing staged → caller handles NO_CHANGE; signal via sentinel.
        diff = self._run("diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            return current or ""
        self._run("commit", "-q", "-m", message)
        new = self.head()
        if not new:
            raise KernelError(COMMIT_FAILED, "commit produced no HEAD")
        return new

    def read_blob_at(self, commit: str, path: str) -> bytes | None:
        proc = self._run("show", f"{commit}:{path}", check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.encode("utf-8")

    def first_parent_commits(self, limit: int = 50) -> list[str]:
        if not self.has_commits():
            return []
        proc = self._run("rev-list", "--first-parent", f"-n{limit}", self.ref)
        return [line for line in proc.stdout.split("\n") if line]

    def show_message(self, commit: str) -> str:
        return self._run("log", "-1", "--format=%B", commit).stdout
