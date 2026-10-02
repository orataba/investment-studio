"""Historical comparisons preserve production risk without retaining UI histories."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import holdings_workspace, instrument_charts, risk_model, source_cache, workspace_cache
from tests.test_base_currency_risk import monetary, risk_fx_history
from tests.test_risk_model import AS_OF_DATE, _holding, _risk_policy, _return_points, _workspace
from tests.conftest import REGISTRY_INSTRUMENT_DETAILS


def test_risk_native_history_is_exactly_the_complete_profile_history():
    # Actual quote policies, multiple native currencies, FX and thin histories.
    for detail in [*REGISTRY_INSTRUMENT_DETAILS, {"instrument_id": "empty", "market_data": []}]:
        original = deepcopy(detail)
        full = instrument_charts.build_instrument_holdings_market_profile_from_detail(
            detail, instrument_id=detail["instrument_id"], as_of_date=date(2026, 4, 15),
            holding_start_date=date(2026, 2, 10), calculation_frequency="daily",
        )
        risk = instrument_charts.build_instrument_risk_return_series_from_detail(
            detail, as_of_date=date(2026, 4, 15), calculation_frequency="daily",
        )
        assert risk == full["instrument_return_series_all"]
        assert detail == original


@pytest.mark.parametrize("missing_policy", ["strict", "complete_case_drop"])
@pytest.mark.parametrize("gap", [False, True])
def test_omitting_return_display_keeps_entire_model_covariance_and_signed_fx_results(missing_policy, gap):
    missing = date.fromisoformat(_return_points()[10]["date"]) if gap else None
    history = risk_fx_history(missing=missing)
    rows = [_holding("foreign", .7, currency="HKD"), monetary("cash:HKD", 200_000),
            _holding("domestic", .4, currency="USD"), monetary("pending:HKD", -300_000, "pending_payable")]
    args = dict(as_of_date=AS_OF_DATE, calculation_frequency="daily",
                risk_policy=_risk_policy(missing_return_policy=missing_policy),
                fx_histories={history.instrument_id: history})
    full = risk_model.enrich_holdings_forward_risk(_workspace(deepcopy(rows), base_currency="USD"), **args)
    compact = risk_model.enrich_holdings_forward_risk(_workspace(deepcopy(rows), base_currency="USD"),
                                                    include_return_series=False, **args)
    for row in full["rows"]:
        row["risk_return_series"] = None
    assert compact == full


def test_comparison_shape_never_replaces_full_holdings_and_expires_with_generation(monkeypatch):
    fingerprint = ["one"]
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
    monkeypatch.setattr(workspace_cache, "_snapshot_fingerprint", lambda _pid: tuple(fingerprint))
    monkeypatch.setattr(workspace_cache, "read_workspace_projection", lambda *_: None)
    calls = []
    def read(compact):
        def build():
            calls.append(compact)
            return {"rows": [{"forward_risk_share": 1, **({} if compact else {"price_chart_1y": [1, 2]})}]}
        return workspace_cache.get_cached_holdings_analytics_workspace("p", as_of_date=AS_OF_DATE,
            risk_policy={}, taxonomy_configuration_version=1, builder=build, risk_comparison=compact)
    compact = read(True)
    assert "price_chart_1y" not in compact["rows"][0]
    compact["rows"][0]["forward_risk_share"] = 0
    assert read(False)["rows"][0]["price_chart_1y"] == [1, 2]
    assert read(True)["rows"][0]["forward_risk_share"] == 1
    assert calls == [True, False]
    fingerprint[0] = "two"
    read(True)
    assert calls == [True, False, True]


def test_risk_context_full_response_matches_full_historical_analysis(client, monkeypatch):
    pid = "investment-studio"
    assert client.get(f"/api/portfolios/{pid}/performance").status_code == 200
    original = holdings_workspace.read_holdings_analysis
    def full_history(*args, **kwargs):
        kwargs["risk_comparison"] = False
        return original(*args, **kwargs)
    with monkeypatch.context() as baseline:
        baseline.setattr(holdings_workspace, "read_holdings_analysis", full_history)
        expected = client.get(f"/api/portfolios/{pid}/risk-context")
        assert expected.status_code == 200, expected.text
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
    actual = client.get(f"/api/portfolios/{pid}/risk-context")
    assert actual.status_code == 200, actual.text
    assert actual.json() == expected.json()
    historical = [entry.value for key, entry in source_cache._cache.items()
                  if len(key) > 3 and key[3] == "holdings_risk_comparison"]
    assert historical and historical[0]["rows"]
    assert all(not any(field in row for field in
                      [*holdings_workspace._HOLDINGS_CHART_FIELD_NAMES, *holdings_workspace._HOLDINGS_RETURN_SERIES_FIELD_NAMES])
               for row in historical[0]["rows"])
    # Revisiting the same context reuses only the compact prior-day analysis.
    assert client.get(f"/api/portfolios/{pid}/risk-context").json() == expected.json()
