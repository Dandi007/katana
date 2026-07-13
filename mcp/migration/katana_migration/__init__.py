"""katana-migration — deterministic migration inventory + rehearsal engine (M3a INVENTORIED + M3b REHEARSED phases)."""

from katana_migration.inventory import run_inventory, build_manifest, compute_summary
from katana_migration.rehearsal import RehearsalEngine, run_rehearsal

__all__ = [
    "run_inventory",
    "build_manifest",
    "compute_summary",
    "RehearsalEngine",
    "run_rehearsal",
]