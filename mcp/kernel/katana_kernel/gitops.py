"""gitops: exact Git commits and transaction-scoped fail-stop primitives."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import fcntl
from dataclasses import dataclass


class CASRejectionError(Exception):
    """Raised when expected_base_sha does not match current HEAD."""


class DirtyWorkTreeError(Exception):
    """Raised when a governed mutation starts from a dirty repository."""


class RollbackSafetyError(Exception):
    """Raised when a transaction cannot be rolled back without broad changes."""


class MutationLockError(Exception):
    """Raised when the per-repository governed mutation lock is unavailable."""


@dataclass(frozen=True)
class FileImage:
    """Exact regular-file image used for transaction attestation/preservation."""

    exists: bool
    data: bytes = b""
    mode: int = 0o644


def _run(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True, text=True, timeout=30,
    )


def _run_env(
    repo_root: str,
    env: dict[str, str],
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
        env=env,
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


@contextmanager
def repository_mutation_lock(
    repo_root: str,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.05,
) -> Iterator[None]:
    """Serialize governed mutations across processes for one Git repository."""
    if timeout_seconds < 0 or poll_seconds <= 0:
        raise ValueError("mutation lock timeout must be >= 0 and poll must be > 0")
    common_dir_result = _run(repo_root, "rev-parse", "--git-common-dir")
    if common_dir_result.returncode != 0 or not common_dir_result.stdout.strip():
        detail = common_dir_result.stderr.strip() or "cannot resolve Git common dir"
        raise MutationLockError(detail)
    common_dir = Path(common_dir_result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = Path(repo_root).resolve() / common_dir
    lock_path = common_dir.resolve() / "katana-governed.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout_seconds
    try:
        lock_file = lock_path.open("a+b")
    except OSError as exc:
        raise MutationLockError(f"cannot open governed mutation lock: {exc}") from exc
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MutationLockError(
                        "timed out waiting for governed mutation lock"
                    )
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def require_clean_working_tree(repo_root: str) -> str:
    """Fail closed unless tracked, staged, and untracked state is clean.

    Returns the base HEAD used by the transaction fail-stop guard.
    """
    git_dir = _run(repo_root, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        raise DirtyWorkTreeError("governed mutation requires a Git repository")
    base_sha = head_sha(repo_root)
    try:
        status_result = _run(
            repo_root, "status", "--porcelain=v1", "--untracked-files=all",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise DirtyWorkTreeError(
            f"cannot verify governed repository cleanliness: {exc}"
        ) from exc
    if status_result.returncode != 0:
        detail = status_result.stderr.strip() or "git status failed"
        raise DirtyWorkTreeError(
            f"cannot verify governed repository cleanliness: {detail}"
        )
    if status_result.stdout:
        raise DirtyWorkTreeError(
            "governed mutation rejected: repository has tracked, staged, "
            "or untracked changes"
        )
    return base_sha


def _normalize_transaction_path(repo_root: str, raw_path: str) -> str:
    """Return a repo-relative path, rejecting escape and symlink ambiguity."""
    if not isinstance(raw_path, str) or not raw_path.strip() or "\0" in raw_path:
        raise RollbackSafetyError("transaction path allowlist contains an empty path")

    root = Path(repo_root).resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError as exc:
            raise RollbackSafetyError(
                f"transaction path escapes repository: {raw_path!r}"
            ) from exc

    if candidate in (Path("."), Path("")) or ".." in candidate.parts:
        raise RollbackSafetyError(
            f"transaction path is not a confined file path: {raw_path!r}"
        )

    normalized = Path(os.path.normpath(str(candidate)))
    if normalized.is_absolute() or normalized == Path(".") or ".." in normalized.parts:
        raise RollbackSafetyError(
            f"transaction path is not confined: {raw_path!r}"
        )
    if normalized.parts[0] == ".git":
        raise RollbackSafetyError(
            f"Git internal paths cannot be transaction paths: {raw_path!r}"
        )

    current = root
    for index, part in enumerate(normalized.parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RollbackSafetyError(
                f"cannot inspect transaction path {raw_path!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise RollbackSafetyError(
                f"symlink not allowed in transaction path: {raw_path!r}"
            )
        if index < len(normalized.parts) - 1 and not stat.S_ISDIR(mode):
            raise RollbackSafetyError(
                f"non-directory ancestor in transaction path: {raw_path!r}"
            )
        if index == len(normalized.parts) - 1 and stat.S_ISDIR(mode):
            raise RollbackSafetyError(
                f"directory not allowed as transaction path: {raw_path!r}"
            )

    try:
        (root / normalized).resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise RollbackSafetyError(
            f"transaction path escapes repository: {raw_path!r}"
        ) from exc
    return normalized.as_posix()


def validate_transaction_paths(repo_root: str, paths: list[str]) -> list[str]:
    """Validate and de-duplicate an explicit transaction path allowlist."""
    if not paths:
        raise RollbackSafetyError("transaction path allowlist must not be empty")
    normalized = []
    for raw_path in paths:
        path = _normalize_transaction_path(repo_root, raw_path)
        if path not in normalized:
            normalized.append(path)
    return normalized


def _read_file_image(repo_root: str, path: str) -> FileImage:
    normalized = _normalize_transaction_path(repo_root, path)
    exact_path = Path(repo_root).resolve() / normalized
    try:
        file_stat = exact_path.lstat()
    except FileNotFoundError:
        return FileImage(False)
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise RollbackSafetyError(
            f"transaction target must be a regular file: {normalized!r}"
        )
    if file_stat.st_nlink != 1:
        raise RollbackSafetyError(
            f"hard-linked transaction target is not allowed: {normalized!r}"
        )
    return FileImage(
        True,
        exact_path.read_bytes(),
        stat.S_IMODE(file_stat.st_mode),
    )


def _reject_special_index_state(repo_root: str, path: str) -> None:
    ignored = _run(repo_root, "check-ignore", "-q", "--", path)
    if ignored.returncode == 0:
        raise RollbackSafetyError(
            f"ignored transaction target is not allowed: {path!r}"
        )
    if ignored.returncode not in (0, 1):
        detail = ignored.stderr.strip() or "git check-ignore failed"
        raise RollbackSafetyError(
            f"cannot verify ignore state for {path!r}: {detail}"
        )
    listed = _run(repo_root, "ls-files", "-v", "--", path)
    if listed.returncode == 0 and listed.stdout:
        marker = listed.stdout[0]
        if marker.islower() or marker == "S":
            raise RollbackSafetyError(
                f"assume-unchanged/skip-worktree target is not allowed: {path!r}"
            )


class TransactionJournal:
    """Explicit touched-path journal with byte-exact pre/post images."""

    def __init__(self, repo_root: str, base_sha: str):
        self.repo_root = str(Path(repo_root).resolve())
        self.base_sha = base_sha
        self._preimages: dict[str, FileImage] = {}
        self._expected: dict[str, FileImage] = {}

    def _capture(self, path: str) -> str:
        normalized = _normalize_transaction_path(self.repo_root, path)
        if normalized not in self._preimages:
            _reject_special_index_state(self.repo_root, normalized)
            self._preimages[normalized] = _read_file_image(
                self.repo_root, normalized,
            )
        return normalized

    def capture_path(self, path: str) -> None:
        self._capture(path)

    def record_write(self, path: str, data: bytes, mode: int | None = None) -> None:
        normalized = self._capture(path)
        preimage = self._preimages[normalized]
        expected_mode = mode or (preimage.mode if preimage.exists else 0o644)
        self._expected[normalized] = FileImage(True, bytes(data), expected_mode)

    def confirm_write(self, path: str, data: bytes) -> None:
        normalized = _normalize_transaction_path(self.repo_root, path)
        current = _read_file_image(self.repo_root, normalized)
        if not current.exists or current.data != bytes(data):
            raise RollbackSafetyError(
                f"transaction write postimage mismatch: {normalized!r}"
            )
        self._expected[normalized] = current

    def record_delete(self, path: str) -> None:
        normalized = self._capture(path)
        self._expected[normalized] = FileImage(False)

    def record_rename(self, old_path: str, new_path: str) -> None:
        old_normalized = self._capture(old_path)
        new_normalized = self._capture(new_path)
        source = _read_file_image(self.repo_root, old_normalized)
        if not source.exists:
            raise RollbackSafetyError(
                f"rename source does not exist: {old_normalized!r}"
            )
        self._expected[old_normalized] = FileImage(False)
        self._expected[new_normalized] = source

    def record_new_from_disk(self, path: str) -> None:
        normalized = _normalize_transaction_path(self.repo_root, path)
        if normalized in self._preimages:
            raise RollbackSafetyError(
                f"transaction path already journaled: {normalized!r}"
            )
        _reject_special_index_state(self.repo_root, normalized)
        current = _read_file_image(self.repo_root, normalized)
        if not current.exists:
            raise RollbackSafetyError(
                f"new transaction path does not exist: {normalized!r}"
            )
        self._preimages[normalized] = FileImage(False)
        self._expected[normalized] = current

    def record_disk_state(self, path: str) -> None:
        normalized = self._capture(path)
        self._expected[normalized] = _read_file_image(
            self.repo_root, normalized,
        )

    @property
    def paths(self) -> list[str]:
        return list(self._expected)

    def expected_images(self, paths: list[str]) -> dict[str, FileImage]:
        normalized = validate_transaction_paths(self.repo_root, paths)
        missing = [path for path in normalized if path not in self._expected]
        if missing:
            raise RollbackSafetyError(
                "transaction paths missing explicit journal entries: "
                + ", ".join(missing)
            )
        return {path: self._expected[path] for path in normalized}

    def verify_worktree(self) -> None:
        for path, expected in self._expected.items():
            if _read_file_image(self.repo_root, path) != expected:
                raise RollbackSafetyError(
                    f"transaction path changed outside journal: {path!r}"
                )

    def rollback(self) -> dict:
        if head_sha(self.repo_root) != self.base_sha:
            return {
                "state": "BROKEN",
                "detail": "HEAD changed during mutation; transaction scene preserved",
                "paths": self.paths,
            }
        changed = [
            path for path, preimage in self._preimages.items()
            if _read_file_image(self.repo_root, path) != preimage
        ]
        if changed:
            return {
                "state": "BROKEN",
                "detail": (
                    "live transaction paths changed; automatic filesystem "
                    "rollback is disabled"
                ),
                "paths": changed,
            }
        return {
            "state": "ROLLED_BACK",
            "detail": "mutation failed before changing transaction paths",
            "paths": [],
        }


def changed_transaction_paths(repo_root: str) -> list[str]:
    """Return exact tracked/staged/untracked files changed since HEAD."""
    commands = (
        ("diff", "--name-only", "-z", "HEAD"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    changed: list[str] = []
    for args in commands:
        try:
            result = _run(repo_root, *args)
        except (subprocess.SubprocessError, OSError) as exc:
            raise RollbackSafetyError(
                f"cannot enumerate transaction paths: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "git path enumeration failed"
            raise RollbackSafetyError(
                f"cannot enumerate transaction paths: {detail}"
            )
        for path in result.stdout.split("\0"):
            if path and path not in changed:
                changed.append(path)
    return validate_transaction_paths(repo_root, changed)


def rollback_transaction_paths(
    repo_root: str,
    base_sha: str,
    paths: list[str],
) -> dict:
    """Deprecated fail-stop shim; it never modifies the live filesystem.

    New callers must use ``TransactionJournal`` and surface ``BROKEN`` for
    manual recovery instead of attempting automatic rollback.
    """
    normalized = validate_transaction_paths(repo_root, paths)
    return {
        "state": "BROKEN",
        "detail": "automatic live filesystem rollback is disabled",
        "paths": normalized,
    }


def _restore_tree(repo_root: str) -> None:
    """Retained only as a fail-closed compatibility shim.

    Legacy callers must migrate to ``rollback_transaction_paths`` with an
    explicit base SHA and exact touched-path allowlist.
    """
    raise RollbackSafetyError(
        "repository-wide restore is disabled; transaction-scoped base SHA "
        "and exact paths are required"
    )


def _commit_exact(
    repo_root: str,
    message: str,
    images: dict[str, FileImage],
    *,
    base_sha: str,
    amend: bool,
) -> dict:
    """Build an exact tree in a temporary index and CAS-publish its commit."""
    common_dir_result = _run(repo_root, "rev-parse", "--git-common-dir")
    if common_dir_result.returncode != 0:
        return {"committed": False, "detail": common_dir_result.stderr.strip()}
    common_dir = Path(common_dir_result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = Path(repo_root).resolve() / common_dir
    common_dir = common_dir.resolve()

    fd, index_path = tempfile.mkstemp(prefix="katana-index-", dir=common_dir)
    os.close(fd)
    os.unlink(index_path)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    try:
        if base_sha:
            read_tree = _run_env(repo_root, env, "read-tree", base_sha)
        else:
            read_tree = _run_env(repo_root, env, "read-tree", "--empty")
        if read_tree.returncode != 0:
            return {"committed": False, "detail": read_tree.stderr.strip()}

        for path, image in images.items():
            if image.exists:
                blob = subprocess.run(
                    ["git", "-C", repo_root, "hash-object", "-w", "--stdin"],
                    input=image.data,
                    capture_output=True,
                    timeout=30,
                    env=env,
                )
                if blob.returncode != 0:
                    return {
                        "committed": False,
                        "detail": blob.stderr.decode(errors="replace").strip(),
                    }
                blob_sha = blob.stdout.decode().strip()
                git_mode = "100755" if image.mode & 0o111 else "100644"
                update = _run_env(
                    repo_root,
                    env,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    git_mode,
                    blob_sha,
                    path,
                )
            else:
                update = _run_env(
                    repo_root, env, "update-index", "--force-remove", "--", path,
                )
            if update.returncode != 0:
                return {"committed": False, "detail": update.stderr.strip()}

        tree = _run_env(repo_root, env, "write-tree")
        if tree.returncode != 0:
            return {"committed": False, "detail": tree.stderr.strip()}
        tree_sha = tree.stdout.strip()
        if base_sha:
            base_tree = _run(repo_root, "rev-parse", f"{base_sha}^{{tree}}")
            if base_tree.returncode == 0 and base_tree.stdout.strip() == tree_sha:
                return {"committed": False, "detail": "nothing to commit"}

        parent_args: list[str] = []
        if amend:
            parents = _run(repo_root, "rev-list", "--parents", "-n", "1", base_sha)
            if parents.returncode != 0:
                return {"committed": False, "detail": parents.stderr.strip()}
            for parent_sha in parents.stdout.split()[1:]:
                parent_args.extend(["-p", parent_sha])
        elif base_sha:
            parent_args = ["-p", base_sha]

        commit = _run_env(
            repo_root,
            env,
            "commit-tree",
            tree_sha,
            *parent_args,
            input_text=message,
        )
        if commit.returncode != 0:
            return {
                "committed": False,
                "detail": commit.stderr.strip() or commit.stdout.strip(),
            }
        commit_sha = commit.stdout.strip()

        ref = _run(repo_root, "symbolic-ref", "-q", "HEAD")
        if ref.returncode != 0 or not ref.stdout.strip():
            return {"committed": False, "detail": "detached HEAD is not supported"}
        old_value = base_sha or ("0" * 40)
        publish = _run(
            repo_root,
            "update-ref",
            ref.stdout.strip(),
            commit_sha,
            old_value,
        )
        if publish.returncode != 0:
            return {
                "committed": False,
                "detail": publish.stderr.strip() or "HEAD CAS publish failed",
            }

        # Synchronize only transaction entries in the real index. Concurrent
        # staged entries outside the allowlist remain untouched and uncommitted.
        for path, image in images.items():
            if image.exists:
                tree_entry = _run(
                    repo_root, "ls-tree", commit_sha, "--", path,
                )
                fields = tree_entry.stdout.split()
                if tree_entry.returncode != 0 or len(fields) < 3:
                    return {
                        "committed": False,
                        "detail": f"cannot resolve committed path: {path}",
                    }
                sync = _run(
                    repo_root,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    fields[0],
                    fields[2],
                    path,
                )
            else:
                sync = _run(
                    repo_root, "update-index", "--force-remove", "--", path,
                )
            if sync.returncode != 0:
                return {
                    "committed": False,
                    "detail": sync.stderr.strip() or "exact index sync failed",
                }
        return {"committed": True, "detail": commit_sha}
    finally:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass


def git_commit(
    repo_root: str,
    message: str,
    paths: list[str],
    *,
    expected_images: dict[str, FileImage] | None = None,
    expected_base_sha: str | None = None,
) -> dict:
    try:
        base_sha = head_sha(repo_root)
        if expected_base_sha is not None and base_sha != expected_base_sha:
            return {"committed": False, "detail": "HEAD changed before exact commit"}
        normalized = validate_transaction_paths(repo_root, paths)
        images = expected_images or {
            path: _read_file_image(repo_root, path) for path in normalized
        }
        if set(images) != set(normalized):
            raise RollbackSafetyError("exact commit images do not match path allowlist")
        return _commit_exact(
            repo_root,
            message,
            images,
            base_sha=base_sha,
            amend=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}


def amend_commit(
    repo_root: str,
    paths: list[str],
    *,
    expected_images: dict[str, FileImage] | None = None,
    expected_base_sha: str | None = None,
) -> dict:
    try:
        base_sha = head_sha(repo_root)
        if expected_base_sha is not None and base_sha != expected_base_sha:
            return {"committed": False, "detail": "HEAD changed before exact amend"}
        if not base_sha:
            return {"committed": False, "detail": "cannot amend unborn HEAD"}
        normalized = validate_transaction_paths(repo_root, paths)
        images = expected_images or {
            path: _read_file_image(repo_root, path) for path in normalized
        }
        if set(images) != set(normalized):
            raise RollbackSafetyError("exact amend images do not match path allowlist")
        message_result = _run(repo_root, "log", "-1", "--format=%B", base_sha)
        if message_result.returncode != 0:
            return {"committed": False, "detail": message_result.stderr.strip()}
        return _commit_exact(
            repo_root,
            message_result.stdout,
            images,
            base_sha=base_sha,
            amend=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}
