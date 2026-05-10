"""Yungu Watchlist backend package."""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"

if INSTRUMENT_CORE_PYTHON.exists():
    instrument_core_path = str(INSTRUMENT_CORE_PYTHON)
    if instrument_core_path not in sys.path:
        sys.path.insert(0, instrument_core_path)
