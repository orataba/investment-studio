import importlib

import pytest
from fastapi.testclient import TestClient

from portfolio_app.core.settings import Settings, get_settings


def test_research_requires_explicit_enablement(monkeypatch):
    monkeypatch.delenv("INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED")
    assert Settings().research_enabled is False


@pytest.mark.parametrize("enabled", [False, True])
def test_research_deployment_boundary(monkeypatch, enabled):
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED", str(enabled).lower())
    get_settings.cache_clear()
    import portfolio_app.main as main_module

    main_module = importlib.reload(main_module)
    with TestClient(main_module.app) as client:
        assert client.get("/api/capabilities").json() == {"research_enabled": enabled}
        assert client.get("/api/portfolios").status_code == 200
        summary = client.get("/api/workspace/summary?portfolio_id=investment-studio")
        assert summary.status_code == 200
        sections = {item["href"] for item in summary.json()["sections"]}
        assert ("/research" in sections) is enabled

        research_paths = {
            path for path in client.get("/openapi.json").json()["paths"]
            if "/research/" in path
        }
        assert bool(research_paths) is enabled
        if enabled:
            response = client.get("/api/portfolios/investment-studio/research/workbench")
            assert response.status_code == 200
        else:
            requests = [
                ("GET", "workbench"),
                ("PUT", "settings"),
                ("POST", "runs"),
                ("GET", "runs/private-run"),
                ("GET", "runs/private-run/benchmark-comparison?benchmark_instrument_id=SPY"),
                ("GET", "artifacts/content?path=private-result.html"),
                ("PUT", "instruments/private-instrument/eligibility"),
            ]
            for method, suffix in requests:
                response = client.request(
                    method, f"/api/portfolios/investment-studio/research/{suffix}",
                    **({"json": {}} if method in {"PUT", "POST"} else {}),
                )
                assert response.status_code == 404, (method, suffix, response.text)
