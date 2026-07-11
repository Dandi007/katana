"""Domain policy protocol + composition-root guard (design §4.2, INV-3, INV-7).

The kernel depends on this *protocol only*; it never imports a concrete domain
policy. Each app statically composes the shared kernel with exactly one domain
policy. ``DomainPolicy`` expresses the two things a domain must own:

- ``plan`` — expand a governed command / fs_* op into a MutationBatch of
  affected resources (design §5.5 step 4).
- ``validate`` — synchronous hard-invariant check over the projected batch
  (design §5.5 step 6); advisory lint only appends warnings.

Commit-stage policy must be pure/deterministic (no LLM, Search, remote, network).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .batch import MutationBatch


@runtime_checkable
class DomainPolicy(Protocol):
    domain: str
    id_prefix: str
    policy_version: int

    def validate(self, batch: MutationBatch) -> None:
        """Raise KernelError(POLICY_VIOLATION/INVALID_CONTENT) on hard violation."""
        ...


class AppComposition:
    """A composition root binds the kernel to exactly one domain policy.

    Constructing with anything other than a single DomainPolicy fails closed,
    giving the static "one app, one domain" guarantee a machine anchor.
    """

    def __init__(self, policy: DomainPolicy) -> None:
        if not isinstance(policy, DomainPolicy):
            raise TypeError(
                "AppComposition requires a single DomainPolicy instance; "
                f"got {type(policy)!r}")
        self.policy = policy
        self.domain = policy.domain
