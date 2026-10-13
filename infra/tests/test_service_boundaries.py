"""Background work and agents must not load HTTP routes to reuse business logic."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_business_services_do_not_import_http_routes():
    violations = []
    for package in ("watchlist", "portfolio", "briefing"):
        source = ROOT / "apps" / package / "backend" / f"{package}_app"
        for path in (source / "services").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imports = [module, *(f"{module}.{alias.name}" for alias in node.names)]
                else:
                    continue
                if any(name == "api.routes" or name.startswith("api.routes.") or ".api.routes" in name for name in imports):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, "Move shared business logic out of HTTP routes: " + ", ".join(violations)
