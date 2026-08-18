"""katana-work-folder-mcp：work-folder 的 MCP server（FastMCP, streamable-http）。

结构化 mutation logger（:func:`log_mutation`）放在包根而非 ``server.py``
surface，是对冻结 spec §5.3 的一处有意偏离（返工说明落档于此）：它包裹
``FSTools._call_mutate`` 与 ``WorkFolderStore._call_mutate`` 两处
``kernel.mutate`` 调用点，从而用一条成功行 + 一条失败行覆盖两个工具面的每
一次受治理 mutation（fs_* 与 wf_*），而不是只覆盖 ``server._server_mutation``
这一层。代价是任何在进入 ``kernel.mutate`` 之前就抛出的失败不会经此落行；
该失败仍由 ``server._server_mutation`` 收口为受控 envelope。
"""

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

    记录经 ``katana_work_folder_mcp`` logger 以 INFO 级别发出；server 的
    ``_configure_logging`` 负责把它抬到 INFO 并挂 stderr handler，否则该
    logger 会继承 root 的 WARNING 默认、直接丢弃这些记录。
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
