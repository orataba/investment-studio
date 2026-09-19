from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from threading import Event
from types import SimpleNamespace

import pytest

from portfolio_app.services import source_cache, workspace_cache


@pytest.fixture()
def cache_context(monkeypatch):
    context = {
        "fingerprint": (None, "generation-1", "2026-01-01", "2026-09-06"),
        "factory": object(),
    }
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
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


def test_full_and_compact_holdings_share_complete_analytics_but_refresh_live_status(cache_context, monkeypatch):
    from portfolio_app.api.routes import workspace

    as_of = date(2026, 9, 6)
    builds = []
    live_transactions = []
    overlays = []
    monkeypatch.setattr(workspace, "_require_portfolio", lambda *_a, **_k: {"portfolio_id": "p", "as_of_date": as_of.isoformat()})
    monkeypatch.setattr(workspace, "get_portfolio_risk_policy", lambda _pid: {})
    monkeypatch.setattr(workspace, "analytics_policy_version", lambda _pid: 1)
    monkeypatch.setattr(workspace, "list_transactions", lambda _pid: list(live_transactions))
    monkeypatch.setattr(workspace, "corporate_action_quality_warnings", lambda *_a, **_k: [])
    monkeypatch.setattr(workspace, "instrument_event_task_quality_warnings", lambda _pid: list(overlays))
    monkeypatch.setattr(workspace, "summarize_holdings_operational_status", lambda *_a, **_k: {})
    monkeypatch.setattr(workspace, "_enrich_position_cycle_costs", lambda *_a, **_k: None)

    def derivative_overlay(rows, *, transactions, **_kwargs):
        rows[0]["derivative_observations"] = transactions

    monkeypatch.setattr(workspace, "enrich_derivative_holding_risk", derivative_overlay)
    points = [{"date": "2026-09-05", "value": 0.01}]

    def build(_portfolio, **kwargs):
        builds.append(kwargs["include_details"])
        return {"portfolio_id": "p", "rows": [{
            "instrument_core": {"instrument_id": "asset"},
            "instrument_return_series_all": points,
            "price_chart_6m": points,
            "price_chart_1y": points,
        }]}

    monkeypatch.setattr(workspace, "_build_holdings_analytics_workspace", build)
    compact = workspace.holdings_workspace("p")
    assert compact["detail_level"] == "compact"
    assert "instrument_return_series_all" not in compact["rows"][0]
    assert compact["rows"][0]["price_chart_1y"] == []

    overlays.append("new task")
    live_transactions.append({"observation": "new"})
    full = workspace.holdings_workspace("p", include_details=True)
    assert builds == [True]
    assert full["detail_level"] == "full"
    assert full["rows"][0]["instrument_return_series_all"] == points
    assert full["rows"][0]["price_chart_1y"] == points
    assert full["quality_warnings"] == ["new task"]
    assert full["rows"][0]["derivative_observations"] == live_transactions
    full["rows"][0]["instrument_return_series_all"].clear()
    assert workspace.holdings_workspace("p", include_details=True)["rows"][0]["instrument_return_series_all"] == points


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
    assert source_cache._cache == {}
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
    monkeypatch.setattr(source_cache, "SOURCE_CACHE_MAX_TOTAL_BYTES", 10)
    workspace_cache.get_cached_portfolio_risk_basis("large", as_of_date=None, builder=lambda: {"payload": "large"})
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "large", as_of_date=None, builder=lambda: {"rebuilt": True}
    ) == {"rebuilt": True}


def test_complete_large_holdings_fit_the_shared_total_budget(cache_context):
    payload = {"series": "x" * 4_300_000}
    args = {"as_of_date": date(2026, 9, 6), "risk_policy": {}, "analytics_policy_version": 1}
    assert workspace_cache.get_cached_holdings_analytics_workspace("p", **args, builder=lambda: payload) == payload
    assert workspace_cache.get_cached_holdings_analytics_workspace(
        "p", **args, builder=lambda: pytest.fail("real complete holdings above 2 MiB must remain reusable")
    ) == payload
    assert 4_300_000 < source_cache._cache_total_size_bytes <= source_cache.SOURCE_CACHE_MAX_TOTAL_BYTES


def test_shared_total_budget_evicts_least_recently_used_values(cache_context, monkeypatch):
    monkeypatch.setattr(source_cache, "SOURCE_CACHE_MAX_TOTAL_BYTES", 45)
    for pid in ("first", "second", "third"):
        workspace_cache.get_cached_portfolio_risk_basis(pid, as_of_date=None, builder=lambda: {"value": "x" * 10})
    assert source_cache._cache_total_size_bytes <= 45
    assert len(source_cache._cache) == 2
    assert workspace_cache.get_cached_portfolio_risk_basis(
        "first", as_of_date=None, builder=lambda: {"rebuilt": True}
    ) == {"rebuilt": True}


@pytest.mark.parametrize("outcome", ["current", "source_changes", "failure", "oversized"])
def test_concurrent_reads_share_one_build_without_blocking_other_keys(cache_context, monkeypatch, outcome):
    started, waiting, release = Event(), Event(), Event()
    builds = []

    class ObservedFuture(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(source_cache, "Future", ObservedFuture)
    if outcome == "oversized":
        monkeypatch.setattr(source_cache, "SOURCE_CACHE_MAX_TOTAL_BYTES", 1)

    def build():
        captured = cache_context["fingerprint"]
        builds.append(captured)
        if len(builds) == 1:
            started.set()
            assert release.wait(5)
            if outcome == "failure":
                raise ValueError("calculation unavailable")
        return {"generation": captured, "rows": [1]}

    def read():
        return workspace_cache.get_cached_portfolio_risk_basis("p", as_of_date=None, builder=build)

    with ThreadPoolExecutor(max_workers=3) as pool:
        owner = pool.submit(read)
        try:
            assert started.wait(5)
            follower = pool.submit(read)
            assert waiting.wait(5)
            independent = pool.submit(lambda: workspace_cache.get_cached_portfolio_risk_basis(
                "another-portfolio", as_of_date=None, builder=lambda: {"independent": True}
            ))
            assert independent.result(timeout=5) == {"independent": True}
            if outcome == "source_changes":
                cache_context["fingerprint"] = ("generation-2",)
        finally:
            release.set()
        if outcome == "failure":
            for task in (owner, follower):
                with pytest.raises(ValueError, match="calculation unavailable"):
                    task.result(timeout=5)
            assert len(builds) == 1
            assert read()["rows"] == [1]
        else:
            first, second = owner.result(timeout=5), follower.result(timeout=5)
            if outcome == "source_changes":
                assert first["generation"] != second["generation"]
                assert len(builds) == 2
            else:
                assert first == second
                first["rows"].clear()
                assert second["rows"] == [1]
                assert len(builds) == 1
    assert source_cache._inflight == {}


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
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
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
