from __future__ import annotations

import re
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
RUNTIME_IMPORT_PATTERNS = (
    r"^\s*from\s+app(?:\.|\s)",
    r"^\s*import\s+app(?:\.|\s|$)",
)


def test_watchlist_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        source = migration_path.read_text(encoding="utf-8")
        assert not any(
            re.search(pattern, source, re.MULTILINE)
            for pattern in RUNTIME_IMPORT_PATTERNS
        ), f"{migration_path.name} must remain self-contained"
