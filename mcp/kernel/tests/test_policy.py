"""Unit tests for DomainPolicy."""

import pytest

from katana_kernel.policy import DomainPolicy, PolicyViolationError


def _invariant(domain, op, args):
    if op == "create" and "body" in args:
        if "## Fact" not in args["body"]:
            raise ValueError("body must contain '## Fact' section")


def test_policy_allows_whitelisted_op():
    policy = DomainPolicy("memory", {"create", "read"})
    policy.verify("create", {})


def test_policy_rejects_unlisted_op():
    policy = DomainPolicy("memory", {"create", "read"})
    with pytest.raises(PolicyViolationError, match="not allowed"):
        policy.verify("delete", {})


def test_policy_invariant_blocks_bad_args():
    policy = DomainPolicy("memory", {"create"}, invariants=[_invariant])
    with pytest.raises(ValueError, match="## Fact"):
        policy.verify("create", {"body": "no fact"})


def test_policy_invariant_passes_good_args():
    policy = DomainPolicy("memory", {"create"}, invariants=[_invariant])
    policy.verify("create", {"body": "## Fact\ntest"})