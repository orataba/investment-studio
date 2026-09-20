"""Narrow risk reads retain catalog eligibility and every signed exposure."""
from copy import deepcopy
from datetime import timedelta

import pytest

from investment_studio_instrument_core.fx_contract import fx_instrument_identity
from investment_studio_instrument_core.fx_rates import latest_spot_point_from_instrument
from portfolio_app.services import derivative_holding_risk, holdings_workspace, instrument_registry, tail_risk

from .test_tail_risk import AS_OF, holding, points, verified_calendar_days, workspace
from .test_valuation_fx_module import _fx_detail


def _details(monkeypatch):
    metadata = verified_calendar_days(monkeypatch)
    start = AS_OF - timedelta(days=40)
    details = {}
    for iid, currency, initial in [("fx-usd-cny", "CNY", 7), ("fx-usd-eur", "EUR", .9)]:
        details[iid] = _fx_detail(iid, currency, [
            ((start + timedelta(days=i)).isoformat(), initial * 1.01**i) for i in range(41)
        ])
        details[iid].update(metadata)
    return details, metadata


def _catalog(details):
    # Match the existing complete-history catalog boundary independently of
    # lazy discovery; do not accidentally admit a pair with a future bad row.
    rates = []
    for iid, detail in details.items():
        identity = fx_instrument_identity(iid)
        if identity and detail and latest_spot_point_from_instrument(detail, detail.get("market_data")):
            rates.append({"source_kind": "direct", "base_currency": identity.base_currency,
                          "quote_currency": identity.quote_currency, "instrument_id": iid})
    return {"rates": rates}


def _read(monkeypatch, source, details, metadata):
    reads = []

    def load(ids):
        reads.extend(ids)
        return {iid: deepcopy(details.get(iid)) for iid in ids}

    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", load)
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_metadata", lambda ids: {
        iid: deepcopy(metadata) for iid in ids
    })
    monkeypatch.setattr(instrument_registry, "get_shared_fx_rates", lambda: pytest.fail("global FX catalog read"))
    return tail_risk.read_portfolio_tail_risk("p", workspace=source, lookback_days=40), reads


@pytest.mark.parametrize(("currency", "base", "expected_ids"), [
    ("USD", "CNY", ["fx-usd-cny"]),
    ("CNY", "USD", ["fx-usd-cny"]),
    ("EUR", "CNY", ["fx-usd-eur", "fx-usd-cny"]),
])
@pytest.mark.parametrize("change", ["valid", "missing_boundary", "future_bad_value", "future_bad_currency",
                                    "duplicate_latest", "invalid_unit", "invalid_scale", "unregistered"])
def test_lazy_fx_matches_full_catalog_and_strict_history(monkeypatch, currency, base, expected_ids, change):
    details, metadata = _details(monkeypatch)
    target = details[expected_ids[0]]
    if change == "missing_boundary":
        del target["market_data"][20]
    elif change in {"future_bad_value", "future_bad_currency"}:
        target["market_data"].append({**target["market_data"][-1],
            "as_of_date": (AS_OF + timedelta(days=1)).isoformat(),
            **({"value": "-1"} if change == "future_bad_value" else {"currency": "GBP"})})
    elif change == "duplicate_latest":
        target["market_data"].append({**target["market_data"][-1], "provider": "another-provider"})
    elif change == "invalid_unit":
        target["market_data"][10]["price_unit"] = "percent"
    elif change == "invalid_scale":
        target["market_data"][10]["price_scale"] = 100
    elif change == "unregistered":
        details[expected_ids[0]] = None
    source = workspace([holding(currency=currency, returns=points([-.01] * 40, start=AS_OF - timedelta(days=40)))], base=base)
    expected = tail_risk.project_portfolio_tail_risk(source, lookback_days=40,
        instrument_details={**details, "a": metadata}, fx_payload=_catalog(details))
    actual, reads = _read(monkeypatch, source, details, metadata)
    assert actual == expected
    assert reads == expected_ids
    if change == "valid":
        assert actual["observation_count"] == 40
    elif change == "missing_boundary":
        assert actual["observation_count"] == 38
    else:
        assert actual["status"] == "unavailable"
        assert actual["rows"][0]["reason"] == "missing_aligned_fx_returns"


def test_foreign_cash_only_retains_initially_empty_lazy_details(monkeypatch):
    details, metadata = _details(monkeypatch)
    row = holding(None, 1000, currency="USD", holding_category="cash_and_settlement", line_id="settled-usd")
    row["instrument_core"].pop("instrument_id")
    source = workspace([row])
    # _read supplies an empty mapping when the cash row has no registry ID.
    actual, reads = _read(monkeypatch, source, details, metadata)
    expected = tail_risk.project_portfolio_tail_risk(source, lookback_days=40,
        instrument_details=details, fx_payload=_catalog(details))
    assert actual == expected and actual["observation_count"] == 40
    assert reads == ["fx-usd-cny"]


def test_held_fx_thin_metadata_does_not_replace_required_observations(monkeypatch):
    details, metadata = _details(monkeypatch)
    source = workspace([holding("fx-usd-cny", currency="CNY", returns=points([-.01] * 40)),
                        holding("foreign", currency="USD", holding_category="cash_and_settlement")])
    actual, reads = _read(monkeypatch, source, details, metadata)
    assert actual["rows"][1]["status"] == "modeled"
    assert reads == ["fx-usd-cny"]


def test_no_fx_reads_for_native_or_excluded_positions(monkeypatch):
    details, metadata = _details(monkeypatch)
    source = workspace([holding(returns=points([-.01] * 40, start=AS_OF - timedelta(days=40))),
                        holding("fcn", 200, currency="EUR", derivative_contract_id="fcn"),
                        holding("cash", 800, holding_category="cash_and_settlement")])
    actual, reads = _read(monkeypatch, source, details, metadata)
    assert reads == []
    assert actual["excluded_gross_exposure"] == 200


def test_narrow_read_matches_live_derivative_overlay_without_loading_its_quotes(monkeypatch):
    details, metadata = _details(monkeypatch)
    sample = points([-.01] * 40, start=AS_OF - timedelta(days=40))
    rows = [holding("long", 600, returns=sample), holding("short", -200, returns=sample),
            holding("foreign-cash", 100, currency="USD", holding_category="cash_and_settlement"),
            holding("settlement", 90, holding_kind="pending_settlement", holding_category="cash_and_settlement")]
    for iid, value, kind in [("fcn", 300, "fcn"), ("option-long", 20, "option"), ("option-written", -10, "option")]:
        rows.append(holding(iid, value, derivative_contract_id=iid, holding_category="derivatives",
            holding_kind="option_obligation" if iid == "option-written" else "position",
            derivative_contract={"derivative_contract_id": iid, "contract_type": kind,
                                 "contract_name": iid, "terms": {}}))
    source = workspace(rows)
    before = deepcopy(source)
    monkeypatch.setattr(derivative_holding_risk, "_quote_by_instrument", lambda *_a, **_k: ({}, {}))
    overlaid = deepcopy(source)
    derivative_holding_risk.enrich_derivative_holding_risk(overlaid["rows"], transactions=[], as_of_date=AS_OF)
    assert "fcn_risk" in overlaid["rows"][-3] and "option_risk" in overlaid["rows"][-1]
    expected = tail_risk.project_portfolio_tail_risk(overlaid, lookback_days=40,
        instrument_details={**details, **{row["instrument_core"]["instrument_id"]: metadata for row in rows}},
        fx_payload=_catalog(details))

    def analysis(_pid, resolved_date, *, response_projection):
        assert resolved_date == AS_OF
        return deepcopy(response_projection(source))

    monkeypatch.setattr(holdings_workspace, "resolve_holdings_request", lambda *_a: ({"portfolio_id": "p"}, AS_OF))
    monkeypatch.setattr(holdings_workspace, "read_holdings_analysis", analysis)
    monkeypatch.setattr(holdings_workspace, "_public_holdings_workspace_response", lambda *_a, **_k: pytest.fail("operational overlay is unrelated to tail scenarios"))
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_metadata", lambda ids: {iid: metadata for iid in ids})
    monkeypatch.setattr(instrument_registry, "get_registry_instrument_details", lambda ids: {iid: details.get(iid) for iid in ids})
    actual = tail_risk.read_portfolio_tail_risk("p", lookback_days=40)
    assert actual == expected
    assert actual["portfolio_nav"] == 1000
    assert actual["excluded_gross_exposure"] == 330
    assert len(actual["rows"]) == len(rows)
    assert source == before
