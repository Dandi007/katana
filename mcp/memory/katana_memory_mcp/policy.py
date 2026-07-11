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

    def canonical_id(self, path: str, content: bytes) -> str | None:
        """Return the domain-canonical id for a card, or None.

        For Memory the canonical identity is the card's frontmatter ``id``
        (design §5.6). The governed façade adopts this id when a governed
        create/write already declares one, so the catalog binding can never
        diverge from the Markdown source (operator P0 #6).
        """
        if not path.endswith(".md"):
            return None
        try:
            card = store.parse_card(content.decode("utf-8"))
        except (UnicodeDecodeError, AttributeError):
            return None
        if card and card.get("id"):
            return str(card["id"])
        return None

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
            # name/filename consistency (design §5.6): the card filename must
            # match its name field so path and identity stay coherent.
            filename = path.rsplit("/", 1)[-1]
            if filename != f"{card['name']}.md":
                raise KernelError(
                    INVALID_CONTENT,
                    f"card filename {filename!r} does not match name "
                    f"{card['name']!r}", virtual_path=path,
                    resource_id=str(card["id"]),
                    violations=["name/filename mismatch"])
            # ID immutability across updates (design §5.6): compare the id in
            # the before-state (same path) with the after-state id.
            if change.before_content is not None:
                try:
                    before = store.parse_card(
                        change.before_content.decode("utf-8"))
                except UnicodeDecodeError:
                    before = None
                if before and before.get("id") and \
                        before["id"] != card["id"]:
                    raise KernelError(
                        INVALID_CONTENT,
                        f"card id is immutable ({before['id']!r} -> "
                        f"{card['id']!r})", virtual_path=path,
                        resource_id=str(card["id"]),
                        violations=["id changed"])
            # Canonical identity coherence (design §5.3, operator P0 #6): the
            # catalog resource_id bound to this change MUST equal the card's
            # frontmatter id, so identity can never split between the catalog
            # and the Markdown source. Checked last so an id-immutability
            # violation reports the more specific "id changed".
            if change.resource_id and change.resource_id != str(card["id"]):
                raise KernelError(
                    INVALID_CONTENT,
                    f"catalog resource_id {change.resource_id!r} does not match "
                    f"card frontmatter id {card['id']!r}", virtual_path=path,
                    resource_id=change.resource_id,
                    violations=["identity split: catalog id != frontmatter id"])
