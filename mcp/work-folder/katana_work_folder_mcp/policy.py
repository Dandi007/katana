"""Work Folder domain policy (design §4.2, §5.6).

WF hard invariants over a projected MutationBatch:
- ``_brief.md`` control files must be parseable and carry required frontmatter
  (reuse brief.validate_brief);
- stable ``wf-*`` id (minted via the kernel identity prefix);
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


class WorkFolderPolicy:
    domain = DOMAIN
    id_prefix = ID_PREFIX
    policy_version = POLICY_VERSION

    def validate(self, batch: MutationBatch) -> None:
        for change in batch.changes:
            if change.op is Op.DELETE or change.after_content is None:
                continue
            path = change.after_path or ""
            if not path.endswith(_brief.BRIEF_NAME):
                # Only the control file is schema-gated; documents pass through.
                continue
            try:
                text = change.after_content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise KernelError(INVALID_CONTENT,
                                  f"{path} is not valid UTF-8") from e
            problems = _brief.validate_brief(text)
            if problems:
                raise KernelError(INVALID_CONTENT,
                                  f"{path} is not a valid _brief.md",
                                  virtual_path=path, violations=problems)
