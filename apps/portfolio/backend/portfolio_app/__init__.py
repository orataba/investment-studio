"""Yungu Portfolio backend portfolio_app."""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
ASSET_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "asset-core" / "python"

if ASSET_CORE_PYTHON.exists():
    asset_core_path = str(ASSET_CORE_PYTHON)
    if asset_core_path not in sys.path:
        sys.path.insert(0, asset_core_path)
