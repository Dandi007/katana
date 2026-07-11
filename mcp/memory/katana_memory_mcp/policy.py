"""Memory domain policy (design §4.2, §5.6).

Owns Memory's static typed hard invariants over a projected MutationBatch:
- id immutable + minted as ``m-*`` (via kernel identity prefix);
- frontmatter parseable; unknown frontmatter round-trips (store层保证);
- name/filename, status/type and verification lifecycle consistent.

The kernel depends only on the ``DomainPolicy`` protocol — this module imports
the kernel, never the reverse (INV-3).
"""
from __future__ import annotations

from katana_kb_mcp_shared.kernel.batch import MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_memory_mcp import store

DOMAIN = "memory"
ID_PREFIX = "m-"
POLICY_VERSION = 1


class MemoryPolicy:
    domain = DOMAIN
    id_prefix = ID_PREFIX
    policy_version = POLICY_VERSION

    def validate(self, batch: MutationBatch) -> None:
        """Hard invariants over the projected post-state of every card write."""
        for change in batch.changes:
            if change.op is Op.DELETE:
                continue
            if change.after_content is None:
                continue
            path = change.after_path or ""
            if not path.endswith(".md"):
                # non-card artifacts (attachments) are not schema-checked here
                continue
            try:
                text = change.after_content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise KernelError(INVALID_CONTENT,
                                  f"card {path} is not valid UTF-8") from e
            card = store.parse_card(text)
            if card is None:
                raise KernelError(INVALID_CONTENT,
                                  f"card {path} has unparseable frontmatter",
                                  virtual_path=path)
            if not card.get("id"):
                raise KernelError(INVALID_CONTENT,
                                  f"card {path} is missing id",
                                  virtual_path=path)
            if not str(card["id"]).startswith(ID_PREFIX):
                raise KernelError(INVALID_CONTENT,
                                  f"card id must be {ID_PREFIX}* (got {card['id']!r})",
                                  virtual_path=path, resource_id=str(card["id"]))
            if not card.get("name") or not card.get("description"):
                raise KernelError(INVALID_CONTENT,
                                  f"card {path} missing name/description",
                                  virtual_path=path)
            # Reuse store-level scalar validation for status/type/name shape.
            try:
                store._validate(name=card.get("name"), status=card.get("status"),
                                type=card.get("type"),
                                description=card.get("description"),
                                last_verified=card.get("last_verified"))
            except ValueError as e:
                raise KernelError(INVALID_CONTENT, str(e),
                                  virtual_path=path) from e
