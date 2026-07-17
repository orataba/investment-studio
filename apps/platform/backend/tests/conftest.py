from __future__ import annotations

import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_ROOT = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
os.environ.setdefault(
    "PORTFOLIO_OPS_PLATFORM_DATABASE_URL",
    "sqlite+pysqlite:///:memory:",
)
BACKEND_ROOT_STR = str(BACKEND_ROOT)
INSTRUMENT_CORE_ROOT_STR = str(INSTRUMENT_CORE_ROOT)

if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)
if INSTRUMENT_CORE_ROOT_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_ROOT_STR)
sys.path.insert(0, INSTRUMENT_CORE_ROOT_STR)
