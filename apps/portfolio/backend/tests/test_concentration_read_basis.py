from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.services import concentration, derivative_holding_risk, instrument_registry, workspace_cache

from .test_concentration import fcn, group, security, workspace


def _unexpected(*_args, **_kwargs):
    pytest.fail("Concentration must not rebuild holdings analytics or market histories.")


def test_concentration_api_preserves_financial_result_without_analytics(client, monkeypatch):
    portfolio_id = "investment-studio"
    as_of_date = date(2026, 4, 15)
    baseline = client.get("/api/workspace/holdings", params={
        "portfolio_id": portfolio_id, "as_of_date": as_of_date, "include_details": True,
    })
    assert baseline.status_code == 200, baseline.text
    full_workspace = baseline.json()
    expected = concentration.read_portfolio_concentration(
        portfolio_id, as_of_date=as_of_date, workspace=full_workspace,
    )
    monkeypatch.setattr(workspace_routes, "holdings_workspace", _unexpected)
    monkeypatch.setattr(workspace_routes, "_build_holdings_analytics_workspace", _unexpected)
    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", _unexpected)
    monkeypatch.setattr(workspace_routes, "enrich_derivative_holding_risk", _unexpected)
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", _unexpected)
    monkeypatch.setattr(derivative_holding_risk, "get_registry_instrument_details", _unexpected)
    monkeypatch.setattr(derivative_holding_risk, "enrich_derivative_holding_risk", _unexpected)

    response = client.get(f"/api/portfolios/{portfolio_id}/concentration?as_of_date={as_of_date}")
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert response.json()["nav"] > 0
    assert response.json()["scopes"][0]["rows"]

    # A lightweight reader must still respect the common reliable valuation prefix.
    monkeypatch.setattr(workspace_routes, "_require_portfolio", lambda *_args, **_kwargs: {
        "portfolio_id": portfolio_id,
        "as_of_date": "2026-04-14",
        "valuation_blocked_from": "2026-04-15",
        "valuation_blocked_reason": "Required FX is unavailable on 2026-04-15.",
    })
    blocked = client.get(f"/api/portfolios/{portfolio_id}/concentration?as_of_date={as_of_date}")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == "Required FX is unavailable on 2026-04-15."


@pytest.fixture
def reader_basis(monkeypatch):
    long = {**security(value=100_000), "quote_as_of_date": "2026-09-04"}
    short = {**security(value=-100_000, account="two"), "quote_as_of_date": "2026-09-04"}
    funded = fcn(currency="HKD", rate=0.125)
    funded.update(account_id="one", fx_rate_as_of_date="2026-09-07", fx_rate_stale=False)
    funded["derivative_contract"]["terms"].update(
        observation_dates=["2026-09-21", "2026-10-21"],
        coupon_rate=0.12,
        private_terms={"issuer_reference": "private-confirmation", "settlement": {"days": 2}},
    )
    stale = deepcopy(funded)
    stale.update(position_reference_id="stale-F", derivative_contract_id="stale-F", fx_rate_stale=True)
    stale["derivative_contract"].update(derivative_contract_id="stale-F", contract_name="Stale FX FCN")
    cached = workspace([{**long, "quantity": 0, "market_value_base": 0}, funded, stale])
    account_rows = [long, short, funded, stale]
    records = [SimpleNamespace(
        holding_json=deepcopy(row),
        account_id=row.get("account_id"),
        position_reference_id=row["position_reference_id"],
        holding_kind=row["holding_kind"],
        instrument_id=(row.get("instrument_core") or {}).get("instrument_id"),
        derivative_contract_id=row.get("derivative_contract_id"),
        quantity=row["quantity"],
        market_value_base=row["market_value_base"],
    ) for row in account_rows]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalar(self, _statement):
            return None

        def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return records if entity is concentration.PortfolioDailyHoldingSnapshotModel else []

    monkeypatch.setattr(concentration, "get_session_factory", lambda: Session)
    monkeypatch.setattr(concentration, "read_concentration_settings", lambda *_args, **_kwargs: {
        "revision": 0, "rules": [], "fcn_allocations": [],
    })
    monkeypatch.setattr(workspace_routes, "_resolve_holdings_request", lambda *_args, **_kwargs: (
        {"portfolio_id": "p", "as_of_date": cached["as_of_date"]}, date.fromisoformat(cached["as_of_date"]),
    ))
    materialized_reads = []

    def materialized(*args, **kwargs):
        materialized_reads.append((args, kwargs))
        return cached

    monkeypatch.setattr(workspace_cache, "get_cached_materialized_holdings_workspace", materialized)
    name_reads = []

    def summaries(instrument_ids):
        name_reads.append(set(instrument_ids))
        return {iid: {
            "instrument_name": f"Registry name {iid}",
            "currency": "CNY", "market_value_base": 999_999,
            "fx_rate_to_base": 99.0, "fx_rate_as_of_date": "2099-01-01",
            "quote_as_of_date": "2099-01-01", "last_price": 999_999,
        } for iid in instrument_ids}

    monkeypatch.setattr(instrument_registry, "get_registry_instrument_summaries", summaries)
    monkeypatch.setattr(workspace_routes, "holdings_workspace", _unexpected)
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", _unexpected)
    return SimpleNamespace(cached=cached, records=records, materialized_reads=materialized_reads, name_reads=name_reads)


def test_materialized_reader_keeps_gross_exposure_and_dated_fx_when_adding_names(reader_basis):
    before = deepcopy(reader_basis.cached)
    records_before = deepcopy([record.holding_json for record in reader_basis.records])
    result = concentration.read_portfolio_concentration("p", as_of_date=date(2026, 9, 8))

    # Both accounts remain visible even when their position-level net quantity is zero.
    direct = group(result, "security", "A")
    assert direct["exposure_base"] == 200_000
    assert direct["weight"] == pytest.approx(0.2)
    assert {source["account_id"] for source in direct["sources"]} == {"one", "two"}
    assert {source["quote_as_of_date"] for source in direct["sources"]} == {"2026-09-04"}
    nominal = group(result, "fcn", "F")
    assert nominal["exposure_base"] == 25_000
    assert nominal["sources"][0]["fx_rate_as_of_date"] == "2026-09-07"
    assert group(result, "fcn", "stale-F")["exposure_base"] is None
    assert group(result, "fcn", "stale-F")["status"] == "unavailable"
    assert result["as_of_date"] == "2026-09-08"
    assert result["base_currency"] == "USD"
    assert result["nav"] == 1_000_000
    assert result["fcn_contracts"][0]["underlyings"] == [
        {"instrument_id": "A", "name": "Registry name A"},
        {"instrument_id": "B", "name": "Registry name B"},
    ]
    assert reader_basis.name_reads == [{"A", "B"}]
    assert len(reader_basis.materialized_reads) == 1
    assert reader_basis.cached == before
    assert [record.holding_json for record in reader_basis.records] == records_before


def test_supplied_workspace_is_reused_and_existing_underlying_names_are_preserved(reader_basis, monkeypatch):
    provided = deepcopy(reader_basis.cached)
    for row in provided["rows"]:
        if row.get("derivative_contract_id"):
            row["fcn_risk"] = {"underlyings": [
                {"instrument_id": "A", "instrument_name": "Workspace A"},
                {"instrument_id": "B", "name": "Workspace B"},
            ]}
    before = deepcopy(provided)
    monkeypatch.setattr(workspace_routes, "_resolve_holdings_request", _unexpected)
    result = concentration.read_portfolio_concentration("p", workspace=provided)
    assert reader_basis.materialized_reads == []
    assert reader_basis.name_reads == []
    assert result["fcn_contracts"][0]["underlyings"] == [
        {"instrument_id": "A", "name": "Workspace A"},
        {"instrument_id": "B", "name": "Workspace B"},
    ]
    assert group(result, "security", "A")["exposure_base"] == 200_000
    assert provided == before


def test_missing_materialized_basis_is_unavailable_instead_of_an_empty_portfolio(reader_basis, monkeypatch):
    monkeypatch.setattr(workspace_cache, "get_cached_materialized_holdings_workspace", lambda *_args, **_kwargs: None)
    with pytest.raises(concentration.ConcentrationUnavailable) as error:
        concentration.read_portfolio_concentration("p")
    assert error.value.status_code == 503
    assert reader_basis.name_reads == []
