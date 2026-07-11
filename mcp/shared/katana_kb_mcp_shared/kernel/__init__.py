"""Shared deterministic kernel — domain-agnostic mechanics (design §4.2).

This package implements, exactly once, the common mechanics shared by the
Memory, Wiki and Work-Folder apps: virtual-path confinement, stable resource
identity, revision/CAS tokens, MutationBatch, the Git-native transaction
protocol (staging → CAS publish → receipt), the transaction manifest schema,
canonical read/VFS node descriptors and the domain-policy protocol.

INV-6 · Git is canonical.  INV-5 · governed mutations.  INV-8 · single-repo
atomicity.  The kernel MUST NOT import any concrete domain policy (INV-3): it
depends only on the ``DomainPolicy`` protocol in :mod:`.policy`.
"""
from . import errors, identity, manifest, paths, vfs
from .batch import Change, MutationBatch, Op
from .catalog import Catalog
from .facade import FS_FACADE, GovernedVFS
from .errors import KernelError
from .gitrepo import GitRepo
from .manifest import Manifest, build_receipt
from .policy import AppComposition, DomainPolicy
from .projection import ProjectionTracker
from .transaction import TransactionEngine, TransactionResult
from .vfs import NodeDescriptor, describe, render_lines

__all__ = [
    "errors", "identity", "manifest", "paths", "vfs",
    "KernelError", "Change", "MutationBatch", "Op",
    "GitRepo", "Manifest", "build_receipt",
    "AppComposition", "DomainPolicy",
    "Catalog", "GovernedVFS", "FS_FACADE", "ProjectionTracker",
    "TransactionEngine", "TransactionResult",
    "NodeDescriptor", "describe", "render_lines",
]
