from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_database


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parents[2]


def _runtime_python_source() -> str:
    roots = (
        BACKEND_ROOT / "portfolio_app",
        BACKEND_ROOT / "scripts",
        REPOSITORY_ROOT / "apps" / "platform" / "backend" / "platform_app",
        REPOSITORY_ROOT / "apps" / "platform" / "backend" / "scripts",
    )
    return "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in sorted(root.rglob("*.py"))
    )


def test_legacy_portfolio_materializer_modules_are_physically_removed() -> None:
    for relative_path in (
        "portfolio_app/services/daily_snapshots.py",
        "portfolio_app/services/snapshot_selection.py",
        "portfolio_app/services/workspace_cache.py",
        "portfolio_app/services/performance_comparison.py",
        "portfolio_app/services/performance.py",
        "portfolio_app/services/ledger.py",
        "scripts/rebuild_portfolio_daily_snapshots.py",
    ):
        assert not (BACKEND_ROOT / relative_path).exists(), relative_path


def test_runtime_cannot_restore_the_legacy_materializer_protocol() -> None:
    source = _runtime_python_source()
    for forbidden in (
        "PortfolioCalculationStateModel",
        "PortfolioDailySnapshotModel",
        "PortfolioDailyHoldingSnapshotModel",
        "PortfolioDailyContributionSliceModel",
        "portfolio_app.services.daily_snapshots",
        "portfolio_app.services.snapshot_selection",
        "portfolio_app.services.workspace_cache",
        "portfolio_app.services.performance_comparison",
        'post("/snapshots/daily/refresh"',
        'f"{portfolio_api_url}/api/portfolios/snapshots/daily/refresh"',
        "get_portfolio_live_summary",
        "_resolve_live_portfolio_as_of_date",
    ):
        assert forbidden not in source, forbidden


def test_backend_cannot_import_legacy_live_ledger_service() -> None:
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name != "portfolio_app.services.ledger"
                    for alias in node.names
                ), path
            if isinstance(node, ast.ImportFrom):
                assert node.module != "portfolio_app.services.ledger", path
                if node.module == "portfolio_app.services":
                    assert all(alias.name != "ledger" for alias in node.names), path


def test_low_level_revision_append_has_an_explicit_unchecked_boundary() -> None:
    source = _runtime_python_source()
    for forbidden in (
        "append_transaction_revision_batch(",
        "append_transaction_create(",
        "append_transaction_amendment(",
        "append_transaction_delete(",
    ):
        assert forbidden not in source, forbidden


def _functions_calling(path: Path, call_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    callers: set[str] = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == call_name
            for node in ast.walk(function)
        ):
            callers.add(function.name)
    return callers


def test_every_production_unchecked_append_is_wrapped_by_same_transaction_validation() -> None:
    store = BACKEND_ROOT / "portfolio_app/services/portfolio_store.py"
    importer = BACKEND_ROOT / "scripts/import_real_portfolio_from_csv.py"
    append_name = "append_transaction_revision_batch_unchecked"
    validation_name = "validate_prospective_transaction_history"

    store_callers = _functions_calling(store, append_name)
    assert store_callers == {
        "_save_store_to_db",  # environment-gated test-fixture loader
        "copy_portfolio",
        "create_transactions",
        "delete_transactions",
        "update_transaction",
    }
    assert _functions_calling(store, validation_name) == {
        "copy_portfolio",
        "create_transactions",
        "delete_transactions",
        "update_account",
        "update_transaction",
    }
    assert _functions_calling(importer, append_name) == {"main"}
    assert _functions_calling(importer, validation_name) == {"main"}
