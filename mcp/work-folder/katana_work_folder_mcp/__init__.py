"""katana-work-folder-mcp：work-folder 的 MCP server（FastMCP, streamable-http）。"""

import logging

_logger = logging.getLogger(__name__)


def log_mutation(
    op: str,
    *,
    domain: str = "work-folder",
    mutation_id: str | None = None,
    commit_sha: str | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
    detail: str | None = None,
) -> None:
    """Emit a single structured mutation outcome line to the journald-captured log.

    Every governed mutation — success or failure — leaves exactly one
    ``domain``/``op`` line carrying ``mutation_id``, ``commit_sha`` and the
    failure root cause (``error_type``/``error_code``) so that a rejected
    transaction can be attributed after the fact rather than swallowed as a
    generic OPERATION_FAILED.
    """
    _logger.info(
        "governed_mutation domain=%s op=%s mutation_id=%s commit_sha=%s "
        "error_code=%s error_type=%s detail=%s",
        domain,
        op,
        mutation_id or "",
        commit_sha or "",
        error_code or "",
        error_type or "",
        (detail or "").replace("\n", " "),
    )
