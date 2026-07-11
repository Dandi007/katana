"""Wiki domain policy (design §4.2, §5.6).

Wiki hard invariants over a projected MutationBatch:
- governed pages must satisfy WIKI schema/zone/provenance (reuse invariants.py);
- raw immutable zone is exempt (design §4.4, §8.1) — the ``raw/`` / ``转换文档``
  trees are not schema-checked;
- existing content follows a no-regression policy: only *new* governed pages
  are hard-gated, so migration of a large broken-link baseline is not blocked.

The kernel depends only on the ``DomainPolicy`` protocol (INV-3); this module
imports the kernel, never the reverse.
"""
from __future__ import annotations

from katana_kb_mcp_shared.kernel.batch import MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp import pages as _pages

DOMAIN = "wiki"
ID_PREFIX = "w-"
POLICY_VERSION = 1

# Zones exempt from schema validation (raw immutable sources, design §8.1).
RAW_PREFIXES = ("raw/", "转换文档/", "DeepThought/", "inbox/")


def _is_raw(path: str) -> bool:
    tail = path.split("/", 1)[-1] if "/" in path else path
    # match against any path segment prefix
    for pref in RAW_PREFIXES:
        if path.startswith(pref) or f"/{pref}" in path or tail.startswith(pref):
            return True
    return False


class WikiPolicy:
    domain = DOMAIN
    id_prefix = ID_PREFIX
    policy_version = POLICY_VERSION

    def validate(self, batch: MutationBatch) -> None:
        for change in batch.changes:
            if change.op is Op.DELETE or change.after_content is None:
                continue
            path = change.after_path or ""
            if not path.endswith(".md") or _is_raw(path):
                continue
            try:
                text = change.after_content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise KernelError(INVALID_CONTENT,
                                  f"page {path} is not valid UTF-8") from e
            fm, body = _pages.parse_page(text)
            # No-regression: only pages that *declare* a wiki 类型 are hard-gated.
            if not fm or "类型" not in fm:
                continue
            errors = _inv.validate_page(fm, body, require_summary=False,
                                        require_sources=False)
            if errors:
                raise KernelError(INVALID_CONTENT,
                                  f"page {path} violates WIKI schema",
                                  virtual_path=path, violations=errors)
