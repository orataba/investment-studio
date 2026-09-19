from __future__ import annotations

from collections import OrderedDict
from datetime import date
from types import SimpleNamespace

import pytest

from portfolio_app.services import workspace_cache


@pytest.fixture()
def cache_context(monkeypatch):
    context = {
        "fingerprint": (None, "generation-1", "2026-01-01", "2026-09-06"),
        "factory": object(),
    }
    monkeypatch.setattr(workspace_cache, "_cache", OrderedDict())
    monkeypatch.setattr(workspace_cache, "_cache_total_size_bytes", 0)
    monkeypatch.setattr(workspace_cache, "_snapshot_fingerprint", lambda _portfolio_id: context["fingerprint"])
    monkeypatch.setattr(workspace_cache, "get_session_factory", lambda: context["factory"])
    return context


def test_holdings_analytics_cache_keys_policy_date_and_version_and_isolates_edits(cache_context):
    builds = []

    def build():
        builds.append(True)
        return {"holdings": [{"instrument_id": "asset", "scope": {"weight": 1.0}}]}

    args = {
        "as_of_date": date(2026, 9, 6),
        "risk_policy": {"lookback": 252, "weights": {"b": 0.4, "a": 0.6}},
        "analytics_policy_version": 1,
        "builder": build,
    }
    first = workspace_cache.get_cached_holdings_analytics_workspace("p", **args)
    first["holdings"][0]["scope"]["weight"] = 0.0
    first["quality_warnings"] = ["live warning"]
    equivalent = {**args, "risk_policy": {"weights": {"a": 0.6, "b": 0.4}, "lookback": 252}}
    second = workspace_cache.get_cached_holdings_analytics_workspace("p", **equivalent)
    assert second == {"holdings": [{"instrument_id": "asset", "scope": {"weight": 1.0}}]}
    assert len(builds) == 1

    for override in (
        {"as_of_date": date(2026, 9, 5)},
        {"analytics_policy_version": 2},
        {"risk_policy": {"lookback": 126}},
    ):
        workspace_cache.get_cached_holdings_analytics_workspace("p", **{**args, **override})
    assert len(builds) == 4


def test_risk_basis_is_shared_between_consumers_without_aliasing(cache_context):
    first = workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=date(2026, 9, 6), builder=lambda: {"resolved_frequency": "daily", "instruments": ["asset"]}
    )
    first["instruments"].clear()
    second = workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=date(2026, 9, 6), builder=lambda: pytest.fail("same portfolio/date should share the profile")
    )
    assert second["instruments"] == ["asset"]


@pytest.mark.parametrize("change", ["generation", "unavailable", "factory", "initially_unavailable"])
def test_builder_result_is_cached_only_for_unchanged_generation(cache_context, change):
    if change == "initially_unavailable":
        cache_context["fingerprint"] = None

    def build_old():
        if change == "factory":
            cache_context["factory"] = object()
        else:
            cache_context["fingerprint"] = (
                None if change == "unavailable"
                else (None, "generation-2", "2026-01-01", "2026-09-06")
            )
        return {"generation": "old"}

    assert workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=build_old
    ) == {"generation": "old"}
    assert workspace_cache._cache == {}
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=lambda: {"generation": "new"}
    ) == {"generation": "new"}


def test_cache_is_scoped_to_session_factory(cache_context):
    first = workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=lambda: {"database": "one"}
    )
    cache_context["factory"] = object()
    second = workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=lambda: {"database": "two"}
    )
    assert first != second


def test_cached_risk_basis_reuses_unchanged_generation_with_value_size_budget(cache_context, monkeypatch):
    workspace_cache.get_cached_portfolio_risk_basis("p", as_of_date=None, builder=lambda: {"old": True})
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=lambda: pytest.fail("unchanged source must reuse computed values")
    ) == {"old": True}
    monkeypatch.setattr(workspace_cache, "WORKSPACE_CACHE_MAX_VALUE_BYTES", 10)
    workspace_cache.get_cached_portfolio_risk_basis("large", as_of_date=None, builder=lambda: {"payload": "large"})
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "large", as_of_date=None, builder=lambda: {"rebuilt": True}
    ) == {"rebuilt": True}


def test_cache_hit_checks_actual_source_freshness(monkeypatch):
    state = SimpleNamespace(
        portfolio_name="Portfolio",
        daily_snapshot_status="current", refresh_request_id=None,
        refreshed_at="generation-1", refreshed_from=date(2026, 1, 1),
        refreshed_to=date(2026, 9, 6), source_stale=False,
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, _portfolio_id):
            return state

    monkeypatch.setattr(workspace_cache, "get_session_factory", lambda: Session)
    monkeypatch.setattr(workspace_cache, "_cache", OrderedDict())
    monkeypatch.setattr(workspace_cache, "_cache_total_size_bytes", 0)
    checked = []

    def requires_refresh(session, portfolio_id):
        assert isinstance(session, Session)
        checked.append(portfolio_id)
        return state.source_stale

    monkeypatch.setattr(workspace_cache, "_state_requires_refresh", requires_refresh)
    workspace_cache.get_cached_portfolio_risk_basis("p", as_of_date=None, builder=lambda: {"source": "old"})
    state.source_stale = True
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "p", as_of_date=None, builder=lambda: {"source": "new"}
    ) == {"source": "new"}
    assert checked == ["p", "p", "p", "p"]
