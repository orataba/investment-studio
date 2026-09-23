from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
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
    monkeypatch.setattr(workspace_cache, "read_workspace_projection", lambda *_args: None)
    return context


def test_holdings_analytics_cache_keys_policy_date_and_version_and_isolates_edits(cache_context):
    builds = []

    def build():
        builds.append(True)
        return {"holdings": [{"instrument_id": "asset", "scope": {"weight": 1.0}}]}

    args = {
        "as_of_date": date(2026, 9, 6),
        "risk_policy": {"lookback": 252, "weights": {"b": 0.4, "a": 0.6}},
        "taxonomy_configuration_version": 1,
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
        {"taxonomy_configuration_version": 2},
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
    from portfolio_app.services import holdings_workspace as workspace

    as_of = date(2026, 9, 6)
    builds = []
    live_transactions = []
    overlays = []
    monkeypatch.setattr(workspace, "_require_portfolio", lambda *_a, **_k: {"portfolio_id": "p", "as_of_date": as_of.isoformat()})
    monkeypatch.setattr(workspace, "get_portfolio_risk_policy", lambda _pid: {})
    monkeypatch.setattr(workspace, "taxonomy_configuration_version", lambda _pid: 1)
    monkeypatch.setattr(workspace, "list_transactions", lambda _pid: list(live_transactions))
    monkeypatch.setattr(workspace, "corporate_action_quality_warnings", lambda *_a, **_k: [])
    monkeypatch.setattr(workspace, "instrument_event_task_quality_warnings", lambda _pid: list(overlays))
    monkeypatch.setattr(workspace, "summarize_holdings_operational_status", lambda *_a, **_k: {})
    monkeypatch.setattr(workspace, "_enrich_position_cycle_costs", lambda *_a, **_k: None)

    def derivative_overlay(rows, *, transactions, **_kwargs):
        rows[0]["derivative_observations"] = transactions

    monkeypatch.setattr(workspace, "enrich_derivative_holding_risk", derivative_overlay)
    points = [{"date": f"2026-09-{index + 1:02}", "value": index / 100} for index in range(30)]
    analytics = {"portfolio_id": "p", "risk_summary": {"weights": [1.0]}, "rows": [{
        "instrument_core": {"instrument_id": "asset", "identifiers": [{"value": "ASSET"}]},
        **{field: points for field in workspace._HOLDINGS_RETURN_SERIES_FIELD_NAMES},
        **{field: points for field in workspace._HOLDINGS_CHART_FIELD_NAMES},
    }]}

    def build(_portfolio, **kwargs):
        builds.append(kwargs["include_details"])
        return analytics

    monkeypatch.setattr(workspace, "_build_holdings_analytics_workspace", build)
    expected_compact = workspace._public_holdings_workspace_response(
        deepcopy(analytics), include_details=False, transactions=[], as_of_date=as_of,
    )
    compact = workspace.holdings_workspace("p")
    assert compact == expected_compact
    assert compact["detail_level"] == "compact"
    assert "instrument_return_series_all" not in compact["rows"][0]
    assert compact["rows"][0]["price_chart_1y"] == []
    assert len(compact["rows"][0]["price_chart_6m"]) == workspace._COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT
    compact["rows"][0]["price_chart_6m"][0]["value"] = -999
    compact["rows"][0]["instrument_core"]["identifiers"].clear()
    compact["risk_summary"]["weights"].clear()

    overlays.append("new task")
    live_transactions.append({"observation": "new"})
    expected_full = workspace._public_holdings_workspace_response(
        deepcopy(analytics), include_details=True, transactions=list(live_transactions), as_of_date=as_of,
    )
    full = workspace.holdings_workspace("p", include_details=True)
    assert full == expected_full
    assert builds == [True]
    assert len(source_cache._cache) == 1
    assert full["detail_level"] == "full"
    assert full["rows"][0]["instrument_return_series_all"] == points
    assert full["rows"][0]["price_chart_1y"] == points
    assert full["quality_warnings"] == ["new task"]
    assert full["rows"][0]["derivative_observations"] == live_transactions
    assert full["rows"][0]["instrument_core"]["identifiers"] == [{"value": "ASSET"}]
    assert full["risk_summary"]["weights"] == [1.0]
    expected_live_compact = workspace._public_holdings_workspace_response(
        deepcopy(analytics), include_details=False, transactions=list(live_transactions), as_of_date=as_of,
    )
    assert workspace.holdings_workspace("p") == expected_live_compact
    full["rows"][0]["instrument_return_series_all"].clear()
    assert workspace.holdings_workspace("p", include_details=True)["rows"][0]["instrument_return_series_all"] == points
    assert "quality_warnings" not in analytics
    assert "detail_level" not in analytics
    assert "derivative_observations" not in analytics["rows"][0]


def test_compact_projection_does_not_copy_discarded_histories(cache_context):
    from portfolio_app.services import holdings_workspace as workspace

    class History(list):
        def __deepcopy__(self, memo):
            pytest.fail("compact response copied a full history before discarding it")

    history = History({"date": f"point-{index}", "value": index} for index in range(2000))
    analytics = {"portfolio_id": "p", "rows": [{
        "instrument_core": {"instrument_id": "asset"},
        **{field: history for field in workspace._HOLDINGS_RETURN_SERIES_FIELD_NAMES},
        **{field: history for field in workspace._HOLDINGS_CHART_FIELD_NAMES},
    }]}
    response = workspace_cache.get_cached_holdings_analytics_workspace(
        "p", as_of_date=date(2026, 9, 6), risk_policy={}, taxonomy_configuration_version=1,
        builder=lambda: analytics, response_projection=workspace._compact_holdings_workspace_projection,
    )
    sparkline = response["rows"][0]["price_chart_6m"]
    assert len(sparkline) == workspace._COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT
    assert sparkline[0] == history[0] and sparkline[-1] == history[-1]
    sparkline[0]["value"] = -1
    assert history[0]["value"] == 0
    assert next(iter(source_cache._cache.values())).value is analytics
    assert analytics["rows"][0]["instrument_return_series_all"] is history
    assert len(analytics["rows"][0]["price_chart_1y"]) == 2000


def test_tail_projection_copies_only_required_financial_inputs(cache_context):
    from portfolio_app.services.tail_risk import _tail_risk_workspace_projection

    class DisplayHistory(list):
        def __deepcopy__(self, memo):
            pytest.fail("tail-risk copied a display-only history")

    points = [{"start_date": "2026-09-05", "date": "2026-09-06", "value": -.01}]
    analytics = {"portfolio_id": "p", "as_of_date": "2026-09-06", "base_currency": "CNY",
                 "totals": {"nav": 1000}, "rows": [
        {"instrument_core": {"instrument_id": "asset", "currency": "CNY"},
         "market_value_base": 1050, "instrument_return_series_all": {"points": points},
         "price_chart_1y": DisplayHistory([1, 2]), "market_profile": DisplayHistory([3])},
        {"derivative_contract_id": "written-option", "market_value_base": -50,
         "holding_category": "derivatives", "derivative_contract": {"contract_name": "Option"}},
    ]}
    args = {"as_of_date": date(2026, 9, 6), "risk_policy": {}, "taxonomy_configuration_version": 1,
            "builder": lambda: analytics, "response_projection": _tail_risk_workspace_projection}
    first = workspace_cache.get_cached_holdings_analytics_workspace("p", **args)
    assert len(first["rows"]) == 2
    assert first["rows"][1]["market_value_base"] == -50
    assert first["totals"]["nav"] == 1000
    first["rows"][0]["instrument_return_series_all"]["points"][0]["value"] = 999
    first["totals"]["nav"] = 0
    second = workspace_cache.get_cached_holdings_analytics_workspace("p", **args)
    assert second["rows"][0]["instrument_return_series_all"]["points"][0]["value"] == -.01
    assert second["totals"]["nav"] == 1000
    assert next(iter(source_cache._cache.values())).value is analytics


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
    args = {"as_of_date": date(2026, 9, 6), "risk_policy": {}, "taxonomy_configuration_version": 1}
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
