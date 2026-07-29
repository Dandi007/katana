"""Unit tests for ResourceIdLedger."""

import os
import tempfile
from pathlib import Path

import pytest

from katana_kernel.ledger import LedgerError, ResourceIdLedger


def test_ledger_gen_id_format():
    d = tempfile.mkdtemp()
    ledger = ResourceIdLedger(os.path.join(d, "tombstones.json"))
    i = ledger.gen_id(set())
    assert i.startswith("m-")
    assert len(i) == 8


def test_ledger_gen_id_avoids_existing():
    d = tempfile.mkdtemp()
    ledger = ResourceIdLedger(os.path.join(d, "tombstones.json"))
    i1 = ledger.gen_id(set())
    i2 = ledger.gen_id({i1})
    assert i1 != i2


def test_ledger_tombstone_and_check():
    d = tempfile.mkdtemp()
    ledger = ResourceIdLedger(os.path.join(d, "tombstones.json"))
    i = ledger.gen_id(set())
    assert not ledger.is_tombstoned(i)
    ledger.tombstone(i)
    assert ledger.is_tombstoned(i)


def test_ledger_gen_id_avoids_tombstones():
    d = tempfile.mkdtemp()
    ledger = ResourceIdLedger(os.path.join(d, "tombstones.json"))
    i = ledger.gen_id(set())
    ledger.tombstone(i)
    for _ in range(50):
        assert ledger.gen_id({i}) != i


def test_ledger_persistence():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "tombstones.json")
    ledger = ResourceIdLedger(path)
    i = ledger.gen_id(set())
    ledger.tombstone(i)
    ledger2 = ResourceIdLedger(path)
    assert ledger2.is_tombstoned(i)


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        "[]",
        '{"tombstones": "m-deadbe"}',
        '{"tombstones": [1]}',
        '{"tombstones": ["wrong-deadbe"]}',
        '{"tombstones": ["m-deadbee"]}',
    ],
)
def test_ledger_corrupt_or_invalid_payload_fails_closed(payload):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "tombstones.json")
    with open(path, "w", encoding="utf-8") as output:
        output.write(payload)

    with pytest.raises(LedgerError):
        ResourceIdLedger(path)


def test_rollback_tombstone_is_persisted():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "tombstones.json")
    ledger = ResourceIdLedger(path)
    resource_id = "m-deadbe"
    ledger.tombstone(resource_id)

    ledger.rollback_tombstone(resource_id)

    assert not ResourceIdLedger(path).is_tombstoned(resource_id)
    assert not list(Path(d).glob("*.tmp-*"))
