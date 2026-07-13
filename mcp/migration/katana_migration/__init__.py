"""katana-migration — deterministic migration inventory tool (M3a INVENTORIED phase) + rehearsal engine (M3b REHEARSED phase) + proof-gate suite (M3c)."""

from katana_migration.inventory import run_inventory, build_manifest, compute_summary
from katana_migration.rehearsal import run_rehearsal, RehearsalEngine, RehearsalError
from katana_migration.proof_gates import (
    run_all_proof_gates,
    run_parity_gate,
    run_hash_gate,
    run_id_gate,
    run_reference_gate,
    run_integrity_gate,
    run_history_gate,
    run_idempotency_gate,
    run_verification_record_gate,
    build_aggregate_report,
)

__all__ = [
    "run_inventory",
    "build_manifest",
    "compute_summary",
    "run_rehearsal",
    "RehearsalEngine",
    "RehearsalError",
    "run_all_proof_gates",
    "run_parity_gate",
    "run_hash_gate",
    "run_id_gate",
    "run_reference_gate",
    "run_integrity_gate",
    "run_history_gate",
    "run_idempotency_gate",
    "run_verification_record_gate",
    "build_aggregate_report",
]