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


class RuntimeStateConfigurationError(Exception):
    """Raised when opt-in runtime state is tracked by or visible to Git."""


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


def require_exact_git_root(repo_root: str) -> str:
    """Return the canonical Git toplevel or reject a missing/nested root."""

    resolved = Path(repo_root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"Git repository root does not exist: {resolved}")
    result = _run(str(resolved), "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"not an existing Git repository: {resolved}")
    discovered = Path(result.stdout.strip()).resolve()
    if discovered != resolved:
        raise ValueError(
            "configured repo_root must equal the exact Git toplevel: "
            f"{resolved} != {discovered}"
        )
    return str(resolved)


def is_working_tree_clean(
    repo_root: str,
    *,
    allowed_ignored_paths: list[str] | None = None,
    scope_prefixes: list[str] | None = None,
    control_paths: list[str] | None = None,
) -> bool:
    try:
        require_clean_working_tree(
            repo_root,
            allowed_ignored_paths=allowed_ignored_paths,
            scope_prefixes=scope_prefixes,
            control_paths=control_paths,
        )
        return True
    except (DirtyWorkTreeError, subprocess.SubprocessError, OSError):
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


def commit_is_ancestor(
    repo_root: str,
    ancestor_sha: str,
    descendant: str = "HEAD",
) -> bool:
    if not ancestor_sha:
        return False
    result = _run(
        repo_root, "merge-base", "--is-ancestor", ancestor_sha, descendant,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or "git merge-base failed"
    raise RollbackSafetyError(
        f"cannot verify commit ancestry for {ancestor_sha}: {detail}"
    )


def commit_parents(repo_root: str, commit_sha: str) -> list[str]:
    result = _run(repo_root, "rev-list", "--parents", "-n", "1", commit_sha)
    if result.returncode != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or "git rev-list failed"
        raise RollbackSafetyError(
            f"cannot read commit parents for {commit_sha}: {detail}"
        )
    return result.stdout.split()[1:]


def commit_changed_paths(repo_root: str, commit_sha: str) -> list[str]:
    result = _run(
        repo_root,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-z",
        commit_sha,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git diff-tree failed"
        raise RollbackSafetyError(
            f"cannot read changed paths for {commit_sha}: {detail}"
        )
    paths = [path for path in result.stdout.split("\0") if path]
    return validate_transaction_paths(repo_root, paths)


def commit_file_image(
    repo_root: str,
    commit_sha: str,
    path: str,
) -> FileImage:
    """Read one exact regular-file image from a commit tree."""
    normalized = _normalize_transaction_path(repo_root, path)
    try:
        tree = subprocess.run(
            [
                "git",
                "-C",
                repo_root,
                "ls-tree",
                "--full-tree",
                "-z",
                commit_sha,
                "--",
                f":(literal){normalized}",
            ],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RollbackSafetyError(
            f"cannot read committed image for {normalized!r}: {exc}"
        ) from exc
    if tree.returncode != 0:
        detail = tree.stderr.decode(errors="replace").strip()
        raise RollbackSafetyError(
            f"cannot read committed image for {normalized!r}: "
            f"{detail or 'git ls-tree failed'}"
        )
    entries = [entry for entry in tree.stdout.split(b"\0") if entry]
    if not entries:
        return FileImage(False)
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise RollbackSafetyError(
            f"ambiguous committed image for {normalized!r}"
        )
    header, committed_path = entries[0].split(b"\t", 1)
    fields = header.split()
    if (
        len(fields) != 3
        or committed_path.decode(errors="surrogateescape") != normalized
    ):
        raise RollbackSafetyError(
            f"malformed committed image for {normalized!r}"
        )
    mode, object_type, object_sha = fields
    if object_type != b"blob" or mode not in {b"100644", b"100755"}:
        raise RollbackSafetyError(
            f"committed target must be a regular file: {normalized!r}"
        )
    try:
        blob = subprocess.run(
            [
                "git",
                "-C",
                repo_root,
                "cat-file",
                "blob",
                object_sha.decode("ascii"),
            ],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RollbackSafetyError(
            f"cannot read committed blob for {normalized!r}: {exc}"
        ) from exc
    if blob.returncode != 0:
        detail = blob.stderr.decode(errors="replace").strip()
        raise RollbackSafetyError(
            f"cannot read committed blob for {normalized!r}: "
            f"{detail or 'git cat-file failed'}"
        )
    file_mode = 0o755 if mode == b"100755" else 0o644
    return FileImage(True, blob.stdout, file_mode)


def find_commit_with_trailer(
    repo_root: str,
    key: str,
    value: str,
) -> str | None:
    needle = f"{key}: {value}"
    result = _run(
        repo_root,
        "log",
        "-n",
        "1",
        "--format=%H",
        "--fixed-strings",
        f"--grep={needle}",
        "HEAD",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git log failed"
        raise RollbackSafetyError(
            f"cannot search mutation receipt {needle!r}: {detail}"
        )
    return result.stdout.strip() or None


def read_katana_commit_trailers(
    repo_root: str,
    commit_sha: str,
) -> dict[str, str]:
    result = _run(repo_root, "show", "-s", "--format=%B", commit_sha)
    if result.returncode != 0:
        detail = result.stderr.strip() or "git show failed"
        raise RollbackSafetyError(
            f"cannot read mutation receipt for {commit_sha}: {detail}"
        )
    trailers: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("Katana-") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in trailers:
            raise RollbackSafetyError(
                f"duplicate reserved trailer {key!r} in {commit_sha}"
            )
        trailers[key] = value.strip()
    return trailers


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


def _normalize_ignored_allowances(
    repo_root: str,
    paths: list[str] | None,
) -> list[str]:
    root = Path(repo_root).resolve()
    normalized: list[str] = []
    for raw_path in paths or []:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            relative = candidate.resolve(strict=False).relative_to(root)
        except (OSError, ValueError) as exc:
            raise DirtyWorkTreeError(
                f"ignored runtime allowance escapes repository: {raw_path!r}"
            ) from exc
        if (
            relative in {Path("."), Path("")}
            or not relative.parts
            or relative.parts[0] == ".git"
        ):
            raise DirtyWorkTreeError(
                f"invalid ignored runtime allowance: {raw_path!r}"
            )
        value = relative.as_posix().rstrip("/")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _ignored_untracked_paths(repo_root: str) -> list[str]:
    try:
        result = _run(
            repo_root,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise DirtyWorkTreeError(
            f"cannot enumerate ignored repository payload: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "git ls-files failed"
        raise DirtyWorkTreeError(
            f"cannot enumerate ignored repository payload: {detail}"
        )
    return [path for path in result.stdout.split("\0") if path]


def _normalize_scope_prefixes(
    repo_root: str,
    paths: list[str] | None,
) -> list[str]:
    """Return repo-relative, normalized scope/control prefixes.

    Scope prefixes are repo-root-relative path prefixes.  They must stay inside
    the repository and must never target Git internals or the repository root.
    """
    root = Path(repo_root).resolve()
    normalized: list[str] = []
    for raw_path in paths or []:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            relative = candidate.resolve(strict=False).relative_to(root)
        except (OSError, ValueError) as exc:
            raise DirtyWorkTreeError(
                f"scope prefix escapes repository: {raw_path!r}"
            ) from exc
        if (
            relative in {Path("."), Path("")}
            or not relative.parts
            or relative.parts[0] == ".git"
        ):
            raise DirtyWorkTreeError(
                f"invalid scope prefix: {raw_path!r}"
            )
        value = relative.as_posix().rstrip("/")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _path_within(path: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        prefix = prefix.rstrip("/")
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def _porcelain_paths(status_output: str) -> list[str]:
    """Parse the changed paths out of `-z` porcelain v1 status output."""
    paths: list[str] = []
    for raw in status_output.split("\0"):
        if len(raw) < 4:
            continue
        paths.append(raw[3:])
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            result.append(path)
    return result


def require_clean_working_tree(
    repo_root: str,
    *,
    allowed_ignored_paths: list[str] | None = None,
    scope_prefixes: list[str] | None = None,
    control_paths: list[str] | None = None,
) -> str:
    """Fail closed unless tracked, staged, and untracked state is clean.

    With ``scope_prefixes=None`` the whole repository must be clean (legacy
    behavior).  With a non-empty scope, only dirt under ``scope_prefixes`` or
    ``control_paths`` (governance surfaces such as ledgers and INDEX files)
    blocks; dirty content elsewhere is preserved and never touched.

    Returns the base HEAD used by the transaction fail-stop guard.
    """
    git_dir = _run(repo_root, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        raise DirtyWorkTreeError("governed mutation requires a Git repository")
    base_sha = head_sha(repo_root)
    try:
        status_result = _run(
            repo_root,
            "status", "--porcelain=v1", "-z", "--no-renames",
            "--untracked-files=all",
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

    scopes = _normalize_scope_prefixes(repo_root, scope_prefixes)
    controls = _normalize_scope_prefixes(repo_root, control_paths)
    if scopes:
        offending = [
            path
            for path in _porcelain_paths(status_result.stdout)
            if _path_within(path, scopes + controls)
        ]
        if offending:
            raise DirtyWorkTreeError(
                "governed mutation rejected: repository has tracked, staged, "
                "or untracked changes within scope"
            )
    elif status_result.stdout:
        raise DirtyWorkTreeError(
            "governed mutation rejected: repository has tracked, staged, "
            "or untracked changes"
        )

    allowances = _normalize_ignored_allowances(
        repo_root,
        allowed_ignored_paths,
    )
    ignored_scope = scopes + controls
    if scopes:
        unexpected_ignored = [
            path
            for path in _ignored_untracked_paths(repo_root)
            if not _path_within(path, allowances)
            and _path_within(path, ignored_scope)
        ]
    else:
        unexpected_ignored = [
            path
            for path in _ignored_untracked_paths(repo_root)
            if not _path_within(path, allowances)
        ]
    if unexpected_ignored:
        raise DirtyWorkTreeError(
            "governed mutation rejected: repository has ignored untracked "
            f"payload outside runtime state: {unexpected_ignored[0]!r}"
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


def validate_runtime_state_paths(repo_root: str, paths: list[str]) -> list[str]:
    """Validate runtime-only paths and require them to be ignored and untracked.

    Runtime state intentionally lives outside the Git transaction journal.  This
    validator is separate from transaction-path validation so ordinary ignored
    content can never be smuggled into a governed data mutation.
    """
    normalized = validate_transaction_paths(repo_root, paths)
    for path in normalized:
        ignored = _run(
            repo_root, "check-ignore", "-q", "--no-index", "--", path,
        )
        if ignored.returncode == 1:
            raise RuntimeStateConfigurationError(
                f"runtime state path must be ignored by Git: {path!r}"
            )
        if ignored.returncode != 0:
            detail = ignored.stderr.strip() or "git check-ignore failed"
            raise RuntimeStateConfigurationError(
                f"cannot verify runtime state path {path!r}: {detail}"
            )

        tracked = _run(repo_root, "ls-files", "--error-unmatch", "--", path)
        if tracked.returncode == 0:
            raise RuntimeStateConfigurationError(
                f"runtime state path must not be tracked by Git: {path!r}"
            )
        if tracked.returncode != 1:
            detail = tracked.stderr.strip() or "git ls-files failed"
            raise RuntimeStateConfigurationError(
                f"cannot verify runtime state path {path!r}: {detail}"
            )
    return normalized


def validate_runtime_state_tree(repo_root: str, directory: str) -> str:
    """Require an ignored runtime directory to contain no tracked descendants."""
    probe = os.path.join(directory, ".path-probe")
    normalized_probe = validate_runtime_state_paths(repo_root, [probe])[0]
    normalized_directory = Path(normalized_probe).parent.as_posix()
    tracked = _run(repo_root, "ls-files", "--", f"{normalized_directory}/")
    if tracked.returncode != 0:
        detail = tracked.stderr.strip() or "git ls-files failed"
        raise RuntimeStateConfigurationError(
            f"cannot verify runtime state tree {normalized_directory!r}: {detail}"
        )
    if tracked.stdout:
        raise RuntimeStateConfigurationError(
            "runtime state directory contains tracked files: "
            f"{normalized_directory!r}"
        )
    return normalized_directory


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

    def effective_paths(self, paths: list[str]) -> list[str]:
        """Return allowlisted paths whose postimage differs from clean HEAD."""
        normalized = validate_transaction_paths(self.repo_root, paths)
        missing = [path for path in normalized if path not in self._expected]
        if missing:
            raise RollbackSafetyError(
                "transaction paths missing explicit journal entries: "
                + ", ".join(missing)
            )
        return [
            path
            for path in normalized
            if self._expected[path] != self._preimages[path]
        ]

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
