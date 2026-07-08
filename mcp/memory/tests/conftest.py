import pytest

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
