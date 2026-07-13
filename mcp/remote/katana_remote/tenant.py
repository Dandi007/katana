"""Tenant confinement: credential-bound tenant, URL/body cannot switch tenant.

Design §7.2: tenant from credential binding, URL/body/tool arg cannot switch
tenant. Server-side config maps tenant to virtual root; client never submits
host paths. .kb/reserved metadata/Git objects/credentials hidden from VFS.
"""

from __future__ import annotations


class TenantMapping:
    def __init__(self, tenant: str, virtual_root: str, domain: str) -> None:
        self.tenant = tenant
        self.virtual_root = virtual_root
        self.domain = domain


class TenantResolver:
    def __init__(self) -> None:
        self._mappings: dict[str, TenantMapping] = {}

    def register(self, tenant: str, virtual_root: str, domain: str) -> None:
        self._mappings[tenant] = TenantMapping(tenant, virtual_root, domain)

    def resolve(self, tenant: str) -> TenantMapping | None:
        return self._mappings.get(tenant)

    def validate(self, credential_tenant: str, url_tenant: str | None) -> bool:
        if url_tenant is None:
            return True
        return credential_tenant == url_tenant

    def is_known_tenant(self, tenant: str) -> bool:
        return tenant in self._mappings


def validate_tenant_match(credential_tenant: str, claimed_tenant: str | None) -> bool:
    if claimed_tenant is None:
        return True
    return credential_tenant == claimed_tenant