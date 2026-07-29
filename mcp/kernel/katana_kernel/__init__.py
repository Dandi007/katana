"""katana-kernel — deterministic shared kernel for governed domain operations."""

from katana_kernel.policy import DomainPolicy
from katana_kernel.vfs import GovernedVFS
from katana_kernel.ledger import ResourceIdLedger
from katana_kernel.manifest import TransactionManifest
from katana_kernel.gitops import (
    CASRejectionError,
    DirtyWorkTreeError,
    MutationLockError,
    RollbackSafetyError,
    cas_guard,
    git_commit,
    head_sha,
    is_working_tree_clean,
)
from katana_kernel.kernel import GovernedKernel, MutationBrokenError

__all__ = [
    "DomainPolicy",
    "GovernedVFS",
    "ResourceIdLedger",
    "TransactionManifest",
    "git_commit",
    "cas_guard",
    "CASRejectionError",
    "DirtyWorkTreeError",
    "MutationLockError",
    "RollbackSafetyError",
    "is_working_tree_clean",
    "head_sha",
    "GovernedKernel",
    "MutationBrokenError",
]
