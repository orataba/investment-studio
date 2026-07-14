from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_database


BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = BACKEND_ROOT / "portfolio_app"
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}


def _runtime_python_paths() -> list[Path]:
    return sorted(RUNTIME_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _runtime_route_paths() -> set[str]:
    routes: set[str] = set()
    for path in _runtime_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in HTTP_METHODS
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                ):
                    routes.add(decorator.args[0].value)
    return routes


def test_legacy_research_runtime_modules_are_physically_removed() -> None:
    for relative_path in (
        "api/routes/research.py",
        "services/research.py",
        "services/research_solver.py",
    ):
        assert not (RUNTIME_ROOT / relative_path).exists(), relative_path

    for relative_path in (
        "api/routes/allocation_research.py",
        "services/allocation_research.py",
        "services/allocation_research_workbench.py",
        "services/allocation_policy_replay.py",
        "services/allocation_solver.py",
        "services/portfolio_market_data.py",
        "services/risk_math.py",
    ):
        assert (RUNTIME_ROOT / relative_path).is_file(), relative_path


def test_runtime_cannot_import_legacy_research_modules() -> None:
    forbidden_modules = {
        "portfolio_app.api.routes.research",
        "portfolio_app.services.research",
        "portfolio_app.services.research_solver",
    }
    for path in _runtime_python_paths():
        assert _imported_modules(path).isdisjoint(forbidden_modules), path


def test_runtime_exposes_only_canonical_allocation_research_routes() -> None:
    routes = _runtime_route_paths()

    assert not any(route == "/research" or route.startswith("/research/") for route in routes)
    assert "/{portfolio_id}/allocation-research/workbench" in routes
    assert (
        "/{portfolio_id}/allocation-research/runs/{allocation_research_run_id}"
        "/policy-replay/benchmark-comparison"
    ) in routes


def test_runtime_has_no_backtest_semantics() -> None:
    for path in _runtime_python_paths():
        assert "backtest" not in path.read_text(encoding="utf-8").lower(), path


def test_portfolio_risk_cannot_depend_on_allocation_implementation() -> None:
    for relative_path in (
        "services/risk_model.py",
        "services/risk_workspace.py",
    ):
        path = RUNTIME_ROOT / relative_path
        allocation_dependencies = {
            module
            for module in _imported_modules(path)
            if module.startswith("portfolio_app.services.allocation_")
        }
        assert not allocation_dependencies, (path, sorted(allocation_dependencies))
