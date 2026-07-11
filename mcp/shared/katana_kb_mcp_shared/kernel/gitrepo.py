"""Thin Git plumbing wrapper for a single canonical data repo.

Only mechanics live here — no domain semantics. The protected canonical ref
compare-and-swap (CAS) is the single linearization point (design §6.3, INV-4).

The publish path uses *writer-private staging*: blobs are written to the object
store, a post-state tree is built in a throwaway temporary index (never the real
index, never the working tree), a commit object is created, and only then is the
protected ref advanced with an atomic old→new compare-and-swap. Any failure
before the ref update leaves the ref — and therefore all client-visible state —
completely unchanged (design §6.6 "publish 前失败零可见").
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from .errors import COMMIT_FAILED, KernelError

_NULL_SHA = "0" * 40


class GitRepo:
    def __init__(self, root: str, ref: str = "HEAD") -> None:
        self.root = root
        self.ref = ref

    def _run(self, *args: str, check: bool = True,
             input_bytes: bytes | None = None,
             env: dict | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", self.root, *args],
            capture_output=True, timeout=60, input=input_bytes,
            env={**os.environ, **(env or {})},
        )
        if check and proc.returncode != 0:
            raise KernelError(
                COMMIT_FAILED,
                f"git {' '.join(args)} failed: "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or proc.stdout.decode('utf-8', 'replace').strip()}",
            )
        return proc

    def _out(self, *args: str, **kw) -> str:
        return self._run(*args, **kw).stdout.decode("utf-8", "replace")

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

    def branch_ref(self) -> str:
        """Name of the branch HEAD points at (even when unborn)."""
        proc = self._run("symbolic-ref", "-q", "HEAD", check=False)
        name = proc.stdout.decode("utf-8", "replace").strip()
        return name or "refs/heads/master"

    # ── ref reads ─────────────────────────────────────────────────────
    def head(self) -> str | None:
        proc = self._run("rev-parse", "--verify", "-q", self.ref, check=False)
        sha = proc.stdout.decode("utf-8", "replace").strip()
        return sha or None

    def has_commits(self) -> bool:
        return self.head() is not None

    def is_dirty(self) -> bool:
        """True if the tracked working tree / index has uncommitted changes.

        Untracked files are ignored (they are not yet canonical); only tracked
        modifications or staged changes count as a dirty, unknown pre-state.
        """
        proc = self._run("status", "--porcelain", "--untracked-files=no",
                         check=False)
        return bool(proc.stdout.decode("utf-8", "replace").strip())

    # ── object store ──────────────────────────────────────────────────
    def hash_object(self, data: bytes) -> str:
        return self._out("hash-object", "-w", "--stdin",
                         input_bytes=data).strip()

    def read_blob_at(self, commit: str, path: str) -> bytes | None:
        proc = self._run("cat-file", "-p", f"{commit}:{path}", check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout

    # ── writer-private publish (design §6.3) ──────────────────────────
    def publish(self, *, expected_base: str | None, message: str,
                writes: dict[str, bytes], deletes: list[str]) -> str | None:
        """Build the post-state tree privately and CAS-publish it.

        ``writes`` maps repo-relative path → new bytes; ``deletes`` removes
        paths. Returns the new commit SHA, or the unchanged base when the batch
        produces no canonical delta. Raises COMMIT_FAILED (mapped to a CAS
        conflict by the engine) if the ref advanced under us. On any failure the
        protected ref and the working tree are left untouched.
        """
        base = self.head()
        if expected_base is not None and base is not None \
                and expected_base != base:
            raise KernelError(
                COMMIT_FAILED,
                f"canonical ref advanced (base {expected_base[:8]} != head {base[:8]})",
                current_commit=base,
            )

        with tempfile.NamedTemporaryFile(prefix="kb-index-", delete=False) as tf:
            index_path = tf.name
        try:
            env = {"GIT_INDEX_FILE": index_path}
            if base is not None:
                self._run("read-tree", base, env=env)
            else:
                self._run("read-tree", "--empty", env=env)
            for path, data in writes.items():
                blob = self.hash_object(data)
                self._run("update-index", "--add", "--cacheinfo",
                          f"100644,{blob},{path}", env=env)
            for path in deletes:
                # Ignore paths already absent from the tree.
                self._run("update-index", "--force-remove", path, env=env,
                          check=False)
            tree = self._out("write-tree", env=env).strip()
        finally:
            try:
                os.unlink(index_path)
            except OSError:
                pass

        # No canonical delta → do not create a commit.
        if base is not None:
            base_tree = self._out("rev-parse", f"{base}^{{tree}}").strip()
            if tree == base_tree:
                return base

        commit_args = ["commit-tree", tree, "-m", message]
        if base is not None:
            commit_args += ["-p", base]
        new_sha = self._out(*commit_args).strip()

        # Atomic compare-and-swap on the protected ref — the linearization
        # point. update-ref with an explicit old value fails if the ref moved.
        branch = self.branch_ref()
        old = base if base is not None else _NULL_SHA
        stdin = f"update {branch} {new_sha} {old}\n".encode("utf-8")
        proc = self._run("update-ref", "--stdin", input_bytes=stdin, check=False)
        if proc.returncode != 0:
            raise KernelError(
                COMMIT_FAILED,
                f"protected ref CAS failed: "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}",
                current_commit=self.head(),
            )
        return new_sha

    def materialize(self, *, writes: dict[str, bytes], deletes: list[str]) -> None:
        """Reflect the published post-state into the working tree.

        Called only AFTER a successful publish so that canonical reads (which go
        through the working tree) see committed content. Never called on the
        failure path.
        """
        for path in deletes:
            target = os.path.join(self.root, path)
            if os.path.exists(target):
                os.remove(target)
        for path, data in writes.items():
            target = os.path.join(self.root, path)
            os.makedirs(os.path.dirname(target) or self.root, exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)

    def sync_index(self) -> None:
        """Reset the real index to HEAD after a private-staging publish.

        The publish path builds the post-state in a throwaway index, so the
        repo's real index still reflects the old HEAD. Syncing it to the new
        HEAD (working tree already materialised) keeps git status clean so
        the next transaction's dirty-tree guard does not misfire.
        """
        if self.has_commits():
            self._run("read-tree", "HEAD")

    def restore_paths(self, base: str | None, paths: list[str]) -> None:
        """Roll a set of working-tree paths back to their ``base`` state.

        Used to undo an already-materialised domain projection when the publish
        fails, guaranteeing zero client-visible effect (design §6.6).
        """
        for path in paths:
            target = os.path.join(self.root, path)
            blob = self.read_blob_at(base, path) if base is not None else None
            if blob is None:
                if os.path.exists(target):
                    os.remove(target)
            else:
                os.makedirs(os.path.dirname(target) or self.root, exist_ok=True)
                with open(target, "wb") as f:
                    f.write(blob)

    def first_parent_commits(self, limit: int = 200) -> list[str]:
        if not self.has_commits():
            return []
        out = self._out("rev-list", "--first-parent", f"-n{limit}", self.ref)
        return [line for line in out.split("\n") if line]

    def show_message(self, commit: str) -> str:
        return self._out("log", "-1", "--format=%B", commit)
