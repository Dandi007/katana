"""katana-migration — deterministic migration inventory tool (M3a INVENTORIED phase) + rehearsal engine (M3b REHEARSED phase) + proof-gate suite (M3c)."""

from katana_migration.inventory import run_inventory, build_manifest, compute_summary
from katana_migration.rehearsal import run_rehearsal, RehearsalEngine, RehearsalError
from katana_migration.proof_gates import (
    run_all_gates,
    parity_gate,
    hash_gate,
    id_gate,
    reference_gate,
    integrity_gate,
    history_gate,
    idempotency_gate,
)

__all__ = [
    "run_inventory",
    "build_manifest",
    "compute_summary",
    "run_rehearsal",
    "RehearsalEngine",
    "RehearsalError",
    "run_all_gates",
    "parity_gate",
    "hash_gate",
    "id_gate",
    "reference_gate",
    "integrity_gate",
    "history_gate",
    "idempotency_gate",
]