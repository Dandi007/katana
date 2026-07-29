"""Retired direct Work Folder brief mutation entry points.

Work Folder identity files are governed resources.  Historical callers used
``seed_brief`` / ``touch_brief`` (and the ``wf-touch`` console script) to edit
``_brief.md`` by physical path, bypassing policy, CAS, idempotency, manifests
and Git commit.  The flat cutover intentionally leaves compatibility symbols
that fail closed so stale automation cannot silently mutate the data root.

Use the Work Folder MCP lifecycle tools (``wf_create``, ``wf_save``,
``wf_append_progress``) with an opaque ``folder_id`` instead.
"""

from __future__ import annotations


class DirectMutationRetiredError(RuntimeError):
    """A legacy physical-path writer was invoked after the governed cutover."""


_MESSAGE = (
    "direct _brief.md mutation is retired; use Work Folder MCP lifecycle "
    "tools with an opaque folder_id"
)


def seed_brief(*args, **kwargs) -> bool:
    """Fail closed; identity creation is owned by ``wf_create``."""

    del args, kwargs
    raise DirectMutationRetiredError(_MESSAGE)


def touch_brief(*args, **kwargs) -> bool:
    """Fail closed; identity updates are owned by governed MCP mutations."""

    del args, kwargs
    raise DirectMutationRetiredError(_MESSAGE)


def main(argv=None) -> int:
    """Retired CLI compatibility shim; never touches the filesystem."""

    del argv
    print(_MESSAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
