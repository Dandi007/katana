"""Tests for tenant confinement."""
from katana_remote.tenant import (
    TenantResolver,
    TenantMapping,
    validate_tenant_match,
)


def test_validate_tenant_match_same():
    assert validate_tenant_match("tenant-a", "tenant-a")
    assert validate_tenant_match("tenant-a", None)


def test_validate_tenant_match_different():
    assert not validate_tenant_match("tenant-a", "tenant-b")


def test_tenant_resolver_register_and_resolve():
    resolver = TenantResolver()
    resolver.register("tenant-a", "/data/tenant-a", "memory")
    mapping = resolver.resolve("tenant-a")
    assert mapping is not None
    assert mapping.tenant == "tenant-a"
    assert mapping.virtual_root == "/data/tenant-a"
    assert mapping.domain == "memory"


def test_tenant_resolver_unknown():
    resolver = TenantResolver()
    assert resolver.resolve("unknown") is None


def test_tenant_resolver_validate():
    resolver = TenantResolver()
    resolver.register("tenant-a", "/data/tenant-a", "memory")
    assert resolver.validate("tenant-a", "tenant-a")
    assert resolver.validate("tenant-a", None)
    assert not resolver.validate("tenant-a", "tenant-b")


def test_tenant_resolver_is_known():
    resolver = TenantResolver()
    resolver.register("tenant-a", "/data/tenant-a", "memory")
    assert resolver.is_known_tenant("tenant-a")
    assert not resolver.is_known_tenant("tenant-b")