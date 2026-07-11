import katana_kb_mcp_shared


def test_package_imports():
    assert katana_kb_mcp_shared.__all__ == ["config", "vault_search", "kernel"]


def test_kernel_is_exported():
    from katana_kb_mcp_shared import kernel
    # Core kernel surface is importable from the shared package.
    assert hasattr(kernel, "TransactionEngine")
    assert hasattr(kernel, "MutationBatch")
    assert hasattr(kernel, "KernelError")
