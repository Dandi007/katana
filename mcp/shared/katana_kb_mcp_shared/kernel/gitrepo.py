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
    # Operational mirrors that live under .kb but are NOT canonical content:
    # they are rebuildable from Git manifests and must never be enumerated,
    # committed as content, or treated as a dirty pre-state (design §6.6).
    _OPERATIONAL_EXCLUDES = (
        ".kb/receipts.json", ".kb/projection.json", ".kb/epoch",
        ".kb/query-gaps.log",
    )

    def ensure_repo(self) -> None:
        inside = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        )
        if inside.returncode != 0:
            self._run("init", "-q")
            self._run("config", "user.email", "kernel@katana.local")
            self._run("config", "user.name", "Katana Kernel")
        self._ensure_operational_excludes()

    def _ensure_operational_excludes(self) -> None:
        """Keep operational .kb mirrors out of Git's untracked view.

        Written to ``.git/info/exclude`` (local, never a canonical file) so
        ``git status``/``ls-files --others`` ignore them; the tracked
        ``.kb/catalog.json`` is still committed because publish stages it by
        explicit blob, which ignore rules do not affect.
        """
        try:
            git_dir = self._out("rev-parse", "--git-dir").strip()
        except KernelError:
            return
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(self.root, git_dir)
        info = os.path.join(git_dir, "info")
        os.makedirs(info, exist_ok=True)
        exclude = os.path.join(info, "exclude")
        existing = ""
        if os.path.exists(exclude):
            with open(exclude, encoding="utf-8") as f:
                existing = f.read()
        want = [e for e in self._OPERATIONAL_EXCLUDES if e not in existing]
        if want:
            with open(exclude, "a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(want) + "\n")

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

    def object_type_at(self, commit: str, path: str) -> str | None:
        """Return the Git object type at ``commit:path`` — "blob"/"tree"/None.

        Reads come from an immutable Git snapshot, never the mutable working
        tree (design §5.2/§6.1). A path is a canonical file iff it is a blob in
        the pinned tree; symlinks and host paths cannot appear as tracked blobs,
        so this also fails closed on symlink/host-path escape.
        """
        if not path:
            return "tree"
        proc = self._run("cat-file", "-t", f"{commit}:{path}", check=False)
        if proc.returncode != 0:
            return None
        t = proc.stdout.decode("utf-8", "replace").strip()
        return t or None

    def list_tree_at(self, commit: str, path: str = "") -> list[tuple[str, str]]:
        """Direct children of ``commit:path`` as (name, node_type).

        ``node_type`` is "file" for blobs and "dir" for subtrees. Reads the
        canonical tree only — no working-tree enumeration, so out-of-band or
        reserved host entries are never surfaced (design §5.2/§7.2).
        """
        spec = f"{commit}:{path.rstrip('/')}/" if path else f"{commit}:"
        proc = self._run("ls-tree", spec.rstrip("/") if not path else spec,
                         check=False)
        if proc.returncode != 0:
            # Fall back to a non-trailing-slash spec for the requested subtree.
            proc = self._run("ls-tree", f"{commit}:{path}", check=False)
            if proc.returncode != 0:
                return []
        out: list[tuple[str, str]] = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            meta, _, name = line.partition("\t")
            fields = meta.split()
            if len(fields) < 2:
                continue
            node_type = "dir" if fields[1] == "tree" else "file"
            out.append((name, node_type))
        return out

    def list_tree_recursive(self, commit: str) -> list[str]:
        """All blob paths under ``commit`` (repo-relative POSIX). Canonical."""
        proc = self._run("ls-tree", "-r", "--name-only", commit, check=False)
        if proc.returncode != 0:
            return []
        return [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines()
                if ln.strip()]

    def export_head_to(self, dest: str) -> None:
        """Populate ``dest`` with a private copy of the HEAD tree.

        This is the writer-private staging seed: domain tools do their
        projection (create/update/rename card, ingest page + backlink + log,
        wf lifecycle) against this throwaway copy, so the real canonical working
        tree is never touched before the ref CAS publish (design §5.5 step 5,
        §6.6 "publish 前失败零可见"). Empty when there are no commits yet.
        """
        os.makedirs(dest, exist_ok=True)
        if not self.has_commits():
            return
        import tarfile
        import io
        proc = self._run("archive", "--format=tar", "HEAD")
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(dest)

    def checkout_head(self) -> None:
        """Force working tree + index to match HEAD (post-crash recovery).

        Recalibrates the working tree to the canonical commit after a crash
        between ref publish and materialize (design §6.6 "重启后 working
        tree=HEAD").
        """
        if not self.has_commits():
            return
        self._run("read-tree", "HEAD")
        self._run("checkout-index", "-a", "-f")

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

    # ── async remote push (design §6.7, INV-9) ───────────────────────
    def remote_head(self, remote: str) -> str | None:
        """The remote branch tip after a fetch, or None if absent/unreachable."""
        branch = self.branch_ref().rsplit("/", 1)[-1] or "master"
        fetch = self._run("fetch", remote, branch, check=False)
        if fetch.returncode != 0:
            return None
        proc = self._run("rev-parse", "--verify", "-q",
                         f"{remote}/{branch}", check=False)
        sha = proc.stdout.decode("utf-8", "replace").strip()
        return sha or None

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        proc = self._run("merge-base", "--is-ancestor",
                         maybe_ancestor, descendant, check=False)
        return proc.returncode == 0

    def push_fast_forward(self, remote: str) -> dict:
        """Fast-forward-only push to a configured remote (design §6.7).

        Fetches remote ancestry first, then only pushes when local canonical
        head is a descendant of the remote head. If the remote is unreachable
        the attempt is retryable; if the remote has diverged (its head is not an
        ancestor of local) the push FAILS CLOSED with REMOTE_DIVERGED — never an
        automatic merge/rebase/force-push (INV-9).
        """
        head = self.head()
        if head is None:
            return {"pushed": None, "status": "nothing_to_push"}
        branch = self.branch_ref().rsplit("/", 1)[-1] or "master"
        remote_head = self.remote_head(remote)
        if remote_head is not None:
            if remote_head == head:
                return {"pushed": head, "status": "already_synced"}
            if not self.is_ancestor(remote_head, head):
                raise KernelError(
                    "REMOTE_DIVERGED",
                    f"remote {remote} head is not an ancestor of local head; "
                    "refusing automatic merge/rebase/force-push",
                    current_commit=head)
        proc = self._run("push", remote, f"{branch}:{branch}", check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")
            if "non-fast-forward" in err or "fetch first" in err:
                raise KernelError("REMOTE_DIVERGED",
                                  f"remote {remote} diverged: {err.strip()}",
                                  current_commit=head)
            raise KernelError("COMMIT_FAILED",
                              f"push to {remote} failed: {err.strip()}",
                              current_commit=head)
        return {"pushed": head, "status": "synced"}

    def first_parent_commits(self, limit: int = 200) -> list[str]:
        if not self.has_commits():
            return []
        out = self._out("rev-list", "--first-parent", f"-n{limit}", self.ref)
        return [line for line in out.split("\n") if line]

    def show_message(self, commit: str) -> str:
        return self._out("log", "-1", "--format=%B", commit)
