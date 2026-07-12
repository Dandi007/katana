"""Unit tests for ResourceIdLedger."""

import os
import tempfile

from katana_kernel.ledger import ResourceIdLedger


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