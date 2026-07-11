"""Core boundary anchors (design §9.1 Core boundary gate).

Proves the shared kernel does not depend on any concrete domain policy, that a
composition root binds exactly one policy, and that the shared package exposes
no MCP domain tools.
"""
import ast
import pathlib

import pytest

from katana_kb_mcp_shared.kernel.batch import MutationBatch
from katana_kb_mcp_shared.kernel.errors import KernelError
from katana_kb_mcp_shared.kernel.policy import AppComposition, DomainPolicy

_KERNEL_DIR = pathlib.Path(__file__).resolve().parents[1] / "katana_kb_mcp_shared" / "kernel"
_DOMAIN_MODULES = ("katana_memory_mcp", "katana_wiki_mcp", "katana_work_folder_mcp")


def _imported_modules(py: pathlib.Path) -> set[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_kernel_does_not_import_any_domain():
    for py in _KERNEL_DIR.glob("*.py"):
        mods = _imported_modules(py)
        for dm in _DOMAIN_MODULES:
            assert not any(m == dm or m.startswith(dm + ".") for m in mods), \
                f"{py.name} imports domain module for {dm}"


def test_shared_package_exposes_no_mcp_tools():
    # The shared package must not import FastMCP or define MCP tools (INV: the
    # shared package itself exposes no domain tools, design §4.4).
    shared_dir = _KERNEL_DIR.parent
    for py in shared_dir.rglob("*.py"):
        if "tests" in py.parts:
            continue
        mods = _imported_modules(py)
        assert "fastmcp" not in mods, f"{py} imports fastmcp"


class _GoodPolicy:
    domain = "test"
    id_prefix = "t-"
    policy_version = 1

    def validate(self, batch: MutationBatch) -> None:  # pragma: no cover - trivial
        return None


def test_good_policy_satisfies_protocol():
    assert isinstance(_GoodPolicy(), DomainPolicy)
    comp = AppComposition(_GoodPolicy())
    assert comp.domain == "test"


def test_composition_root_rejects_non_policy():
    with pytest.raises(TypeError):
        AppComposition(object())


def test_kernel_error_envelope_shape():
    e = KernelError("REVISION_CONFLICT", "boom", resource_id="m-1",
                    expected_revision="rev-a", actual_revision="rev-b")
    env = e.to_envelope()
    assert env["code"] == "REVISION_CONFLICT"
    assert env["retryable"] is True
    assert env["resource_id"] == "m-1"
    assert "virtual_path" not in env  # unset optionals are omitted
    assert env["violations"] == []
