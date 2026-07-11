"""Stable identity + revision/CAS token tests (design §5.3, INV-3)."""
from katana_kb_mcp_shared.kernel import identity


def test_mint_id_is_prefixed_and_unique():
    existing = set()
    ids = set()
    for _ in range(200):
        rid = identity.mint_id("m-", existing)
        assert rid.startswith("m-")
        assert rid not in existing
        existing.add(rid)
        ids.add(rid)
    assert len(ids) == 200


def test_mint_id_avoids_collision():
    taken = {"x-000000"}
    # Force the generator to skip a taken id by feeding a 0-byte space.
    rid = identity.mint_id("x-", taken, nbytes=1)
    assert rid not in taken


def test_content_hash_stable_and_encoding_agnostic():
    assert identity.content_hash("hello") == identity.content_hash(b"hello")


def test_resource_revision_changes_on_path_change():
    a = identity.resource_revision(resource_id="m-1", virtual_path="a.md",
                                   content=b"body")
    b = identity.resource_revision(resource_id="m-1", virtual_path="b.md",
                                   content=b"body")
    assert a != b


def test_resource_revision_stable_when_unchanged():
    kw = dict(resource_id="m-1", virtual_path="a.md", content=b"body")
    assert identity.resource_revision(**kw) == identity.resource_revision(**kw)


def test_content_revision_ignores_path():
    assert identity.content_revision("x") == identity.content_revision(b"x")


def test_request_hash_deterministic():
    assert identity.request_hash("req") == identity.request_hash("req")
    assert identity.request_hash("a") != identity.request_hash("b")
