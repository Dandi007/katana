"""Ensure sibling maintenance scripts (``scripts/``) are importable.

``scripts`` is intentionally excluded from the setuptools flat-layout package
discovery (see ``mcp/work-folder/pyproject.toml``), so it is only reachable via
``sys.path`` when the surrounding package root is on the path. The full-package
CI run relies on ``mcp/conftest.py`` for this; when ``pytest
mcp/work-folder/tests`` is invoked alone (as the frozen acceptance gate does),
the rootdir becomes ``mcp/work-folder`` and that conftest is not loaded, leaving
``import scripts.migrate_flat`` broken. Mirror ``mcp/memory/tests/conftest.py``
and add the package root here so the gate stays hermetic and green.
"""

import sys
from pathlib import Path

_work_folder = Path(__file__).resolve().parent.parent
if str(_work_folder) not in sys.path:
    sys.path.insert(0, str(_work_folder))