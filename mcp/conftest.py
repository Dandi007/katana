"""conftest: ensure mcp/ packages are importable without pip install."""
import sys
from pathlib import Path

_mcp = Path(__file__).resolve().parent
for _sub in ["kernel", "memory", "shared", "wiki", "work-folder"]:
    _pkg = _mcp / _sub
    if _pkg.is_dir() and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))