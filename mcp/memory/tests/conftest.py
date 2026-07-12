import sys
from pathlib import Path

import pytest

_mcp = Path(__file__).resolve().parent.parent.parent
for _sub in ["kernel", "memory", "shared", "wiki", "work-folder"]:
    _pkg = _mcp / _sub
    if _pkg.is_dir() and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

from katana_memory_mcp import store


@pytest.fixture
def tenant_dir(tmp_path):
    d = tmp_path / "uther"
    d.mkdir()
    return str(d)


@pytest.fixture
def seeded(tenant_dir):
    c1 = store.create_card(tenant_dir, "card-one", "desc one", "## Fact\nA\n\n## How to Verify\nrun a", type="reference", now="2026-07-08")
    c2 = store.create_card(tenant_dir, "card-two", "desc two", "## Fact\nB\n\n## How to Verify\nrun b", now="2026-07-08")
    return tenant_dir, c1, c2
