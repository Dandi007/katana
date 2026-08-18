"""katana-kernel — deterministic shared kernel for governed domain operations."""

from katana_kernel.policy import DomainPolicy
from katana_kernel.vfs import GovernedVFS
from katana_kernel.ledger import LedgerError, ResourceIdLedger
from katana_kernel.manifest import TransactionManifest
from katana_kernel.idempotency import (
    IdempotencyConflictError,
    InvalidMutationTransitionError,
    MutationClaim,
    MutationRecord,
    SQLiteMutationLedger,
    canonical_request_hash,
)
from katana_kernel.gitops import (
    BaseCommitConflictError,
    CASRejectionError,
    DirtyWorkTreeError,
    MutationLockError,
    RollbackSafetyError,
    RuntimeStateConfigurationError,
    cas_guard,
    git_commit,
    head_sha,
    is_working_tree_clean,
    require_exact_git_root,
)
from katana_kernel.kernel import GovernedKernel, MutationBrokenError

__all__ = [
    "DomainPolicy",
    "GovernedVFS",
    "ResourceIdLedger",
    "LedgerError",
    "TransactionManifest",
    "IdempotencyConflictError",
    "InvalidMutationTransitionError",
    "MutationClaim",
    "MutationRecord",
    "SQLiteMutationLedger",
    "canonical_request_hash",
    "git_commit",
    "cas_guard",
    "BaseCommitConflictError",
    "CASRejectionError",
    "DirtyWorkTreeError",
    "MutationLockError",
    "RollbackSafetyError",
    "RuntimeStateConfigurationError",
    "is_working_tree_clean",
    "require_exact_git_root",
    "head_sha",
    "GovernedKernel",
    "MutationBrokenError",
]
