"""katana-kb-mcp 共享层：deterministic kernel + config 接入 + vault-search 客户端。

- ``kernel``：三域共用的 domain-agnostic mechanics（path/identity/CAS/
  MutationBatch/Git transaction/manifest/VFS/policy protocol）。
- ``config``：复用 katana-config.sh 的配置 SSoT。
- ``vault_search``：vault-search HTTP 客户端。
"""
__all__ = ["config", "vault_search", "kernel"]
