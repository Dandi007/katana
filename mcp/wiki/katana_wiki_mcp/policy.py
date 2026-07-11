"""Wiki domain policy (design §4.2, §5.6).

Wiki hard invariants over a projected MutationBatch:

- governed pages (frontmatter declaring a wiki ``类型``) must satisfy WIKI
  schema / zone / provenance / outlink (reuse ``invariants.py``);
- the raw immutable zone (``raw/`` / ``转换文档`` / ``DeepThought`` / ``inbox``)
  is exempt — those trees are not schema-checked (design §4.4, §8.1);
- **no-regression**: for an *existing* governed page, a mutation is only
  rejected when it introduces a NEW violation that was not already present in
  the before-state. This lets ingest add a backlink/log line to a legacy page
  that was already imperfect (large broken-link baseline, design §5.6) without
  the governed pipeline rejecting an otherwise-legitimate update.

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
    for pref in RAW_PREFIXES:
        if path.startswith(pref) or f"/{pref}" in path or tail.startswith(pref):
            return True
    return False


def _errors_for(text: str) -> list[str]:
    """Hard-invariant errors for a wiki page body."""
    fm, body = _pages.parse_page(text)
    if not fm or "类型" not in fm:
        return ["missing 类型"]
    return _inv.validate_page(fm, body, require_summary=False,
                              require_sources=False)


class WikiPolicy:
    domain = DOMAIN
    id_prefix = ID_PREFIX
    policy_version = POLICY_VERSION

    def validate(self, batch: MutationBatch) -> None:
        for change in batch.changes:
            path = change.after_path or change.before_path or ""
            if _is_raw(path) and change.op in {Op.CREATE, Op.WRITE, Op.EDIT, Op.COPY, Op.RENAME, Op.DELETE}:
                raise KernelError(INVALID_CONTENT,
                                  f"raw wiki zone is immutable: {path}",
                                  virtual_path=path, violations=["raw immutable"])
            if change.op is Op.DELETE or change.after_content is None:
                continue
            path = change.after_path or ""
            if path == "log.md" or not path.endswith(".md"):
                continue
            try:
                text = change.after_content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise KernelError(INVALID_CONTENT,
                                  f"page {path} is not valid UTF-8") from e
            after_errors = _errors_for(text)
            if not after_errors:
                continue
            # No-regression: for an existing page, subtract the violations that
            # were already present in the before-state.
            before_errors: list[str] = []
            if change.before_content is not None:
                try:
                    before_errors = _errors_for(
                        change.before_content.decode("utf-8"))
                except UnicodeDecodeError:
                    before_errors = []
            new_violations = [e for e in after_errors if e not in before_errors]
            if new_violations:
                raise KernelError(INVALID_CONTENT,
                                  f"page {path} violates WIKI schema",
                                  virtual_path=path, violations=new_violations)
