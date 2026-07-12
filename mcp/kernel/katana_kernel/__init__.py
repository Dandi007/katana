"""katana-kernel — deterministic shared kernel for governed domain operations."""

from katana_kernel.policy import DomainPolicy
from katana_kernel.vfs import GovernedVFS
from katana_kernel.ledger import ResourceIdLedger
from katana_kernel.manifest import TransactionManifest
from katana_kernel.gitops import git_commit, cas_guard, CASRejectionError, is_working_tree_clean, head_sha
from katana_kernel.kernel import GovernedKernel

__all__ = [
    "DomainPolicy",
    "GovernedVFS",
    "ResourceIdLedger",
    "TransactionManifest",
    "git_commit",
    "cas_guard",
    "CASRejectionError",
    "is_working_tree_clean",
    "head_sha",
    "GovernedKernel",
]