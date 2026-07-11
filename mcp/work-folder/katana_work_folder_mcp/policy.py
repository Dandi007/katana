"""Work Folder domain policy (design §4.2, §5.6).

WF hard invariants over a projected MutationBatch:

- ``_brief.md`` control files must be parseable and carry required frontmatter
  (reuse ``brief.validate_brief``);
- stable WF aggregate identity: a governed update to an existing ``_brief.md``
  MUST NOT change its ``id`` field (design §5.6 "stable wf-*"). The before-state
  id is compared against the after-state id inside the same MutationBatch;
- ``golden-order.md`` is append-only: a governed write may only extend it —
  truncating or rewriting already-recorded golden-order content is rejected
  (design §5.6 "golden-order append-only");
- non-control artifacts (documents, progress notes) are not schema-gated here.

The kernel depends only on the ``DomainPolicy`` protocol (INV-3); this module
imports the kernel, never the reverse.
"""
from __future__ import annotations

from katana_kb_mcp_shared.kernel.batch import MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_work_folder_mcp import brief as _brief

DOMAIN = "work-folder"
ID_PREFIX = "wf-"
POLICY_VERSION = 1

GOLDEN_ORDER_NAME = "golden-order.md"


def _brief_id(text: str) -> str | None:
    try:
        r = _brief.parse_brief(text)
    except _brief.BriefError:
        return None
    return r["frontmatter"].get("id")


class WorkFolderPolicy:
    domain = DOMAIN
    id_prefix = ID_PREFIX
    policy_version = POLICY_VERSION

    def validate(self, batch: MutationBatch) -> None:
        for change in batch.changes:
            if change.op is Op.DELETE or change.after_content is None:
                continue
            path = change.after_path or ""
            try:
                text = change.after_content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise KernelError(INVALID_CONTENT,
                                  f"{path} is not valid UTF-8") from e

            if path.endswith(_brief.BRIEF_NAME):
                self._validate_brief(change, path, text)
            elif path.endswith(GOLDEN_ORDER_NAME):
                self._validate_golden_order(change, path, text)

    def _validate_brief(self, change, path: str, text: str) -> None:
        problems = _brief.validate_brief(text)
        if problems:
            raise KernelError(INVALID_CONTENT,
                              f"{path} is not a valid _brief.md",
                              virtual_path=path, violations=problems)
        # Stable aggregate identity: id is immutable across updates.
        if change.before_content is not None:
            try:
                before_id = _brief_id(change.before_content.decode("utf-8"))
            except UnicodeDecodeError:
                before_id = None
            after_id = _brief_id(text)
            if before_id and after_id and before_id != after_id:
                raise KernelError(
                    INVALID_CONTENT,
                    f"{path}: work-folder id is immutable "
                    f"({before_id!r} -> {after_id!r})",
                    virtual_path=path,
                    violations=["brief id changed"])

    def _validate_golden_order(self, change, path: str, text: str) -> None:
        # Append-only: the before-state must remain a prefix of the after-state.
        if change.before_content is None:
            return
        try:
            before = change.before_content.decode("utf-8")
        except UnicodeDecodeError:
            return
        if not text.startswith(before):
            raise KernelError(
                INVALID_CONTENT,
                f"{path}: golden-order.md is append-only; existing content "
                "may not be rewritten or truncated",
                virtual_path=path,
                violations=["golden-order not append-only"])
