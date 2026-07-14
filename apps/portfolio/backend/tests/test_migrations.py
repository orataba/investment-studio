from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "portfolio_app")


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_portfolio_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        imported_modules = _imported_modules(migration_path.read_text(encoding="utf-8"))
        offenders = sorted(
            module
            for module in imported_modules
            if any(
                module == package or module.startswith(f"{package}.")
                for package in RUNTIME_PACKAGE_NAMES
            )
        )
        assert not offenders, f"{migration_path.name} imports runtime packages: {offenders}"
