import katana_kb_mcp_shared


def test_package_imports():
    assert katana_kb_mcp_shared.__all__ == ["config", "vault_search"]
