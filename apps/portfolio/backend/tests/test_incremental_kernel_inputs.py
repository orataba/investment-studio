"""Prepared daily inputs preserve causal FX observations while doing less work."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import market_data, performance, portfolio_store, valuation_fx


def fx_detail(instrument_id, currency, rate):
    return {
        "instrument_id": instrument_id, "instrument_type": "fx", "currency": currency,
        "source_settings": {"market_calendar": "24/5"},
        "quote_selection_policy": {"valuation": ["spot"]},
        "market_data": [{"metric_family": "fx", "quote_basis": "spot", "as_of_date": f"2026-08-0{day}",
                         "value": str(rate + day / 100), "currency": currency,
                         "price_unit": "rate", "price_scale": 1.0, "status": "complete"}
                        for day in (3, 4, 5, 6)],
    }


@pytest.mark.parametrize("fault", [None, "duplicate", "wrong_currency", "invalid_value", "future_fault", "missing_day"])
@pytest.mark.parametrize("pair", [("USD", "HKD"), ("HKD", "USD"), ("HKD", "CNY")])
def test_indexed_fx_matches_canonical_each_historical_boundary(fault, pair):
    details = {"fx-usd-hkd": fx_detail("fx-usd-hkd", "HKD", 7.8),
               "fx-usd-cny": fx_detail("fx-usd-cny", "CNY", 7.1)}
    history = details["fx-usd-hkd"]["market_data"]
    if fault == "duplicate":
        history.append(deepcopy(history[2]))
    elif fault == "wrong_currency":
        history[2]["currency"] = "EUR"
    elif fault == "invalid_value":
        history[2]["value"] = "nan"
    elif fault == "future_fault":
        history[3]["currency"] = "EUR"
    elif fault == "missing_day":
        history.pop(1)
    originals = deepcopy(details)
    indexed = valuation_fx.HistoricalInstrumentDetails(end_date=date(2026, 8, 5))
    canonical = {}
    for day in (2, 3, 4, 5, 6):
        arguments = dict(as_of_date=date(2026, 8, day), base_currency=pair[0], quote_currency=pair[1],
                         direct_instruments={("USD", "HKD"): "fx-usd-hkd", ("USD", "CNY"): "fx-usd-cny"},
                         instrument_detail_loader=details.get)
        expected = valuation_fx.resolve_fx_rate_on(**arguments, instrument_detail_cache=canonical)
        assert valuation_fx.resolve_fx_rate_on(**arguments, instrument_detail_cache=indexed) == expected
        if day == 2:
            assert expected is None
        if day == 3:
            assert expected is not None
        if day == 5 and fault in {"duplicate", "wrong_currency", "invalid_value"}:
            assert expected is None
    assert details == originals


def test_clean_fx_history_is_validated_once_across_daily_boundaries(monkeypatch):
    detail = fx_detail("fx-usd-hkd", "HKD", 7.8)
    indexed = valuation_fx.HistoricalInstrumentDetails(end_date=date(2026, 8, 6))
    calls = []
    original = market_data.resolve_quote_series

    def track(*args, **kwargs):
        calls.append(kwargs.get("end_date"))
        return original(*args, **kwargs)

    monkeypatch.setattr(market_data, "resolve_quote_series", track)
    for day in (3, 4, 5, 6):
        assert valuation_fx.direct_fx_point_as_of(
            instrument_id="fx-usd-hkd", as_of_date=date(2026, 8, day),
            instrument_detail_cache=indexed, instrument_detail_loader=lambda _: detail,
        )["as_of_date"] == date(2026, 8, day)
    assert calls == [date(2026, 8, 6)]


def test_native_currency_daily_kernel_never_loads_fx_catalog(client, monkeypatch):
    def unexpected():
        raise AssertionError("A same-currency daily calculation needs no FX history.")

    monkeypatch.setattr(performance, "get_shared_fx_rates", unexpected)
    portfolio = portfolio_store.get_portfolio("investment-studio")
    accounts = [account for account in portfolio_store.list_accounts("investment-studio")
                if account["currency"] == portfolio["base_currency"]]
    account_ids = {account["account_id"] for account in accounts}
    transactions = [tx for tx in portfolio_store.list_transactions("investment-studio")
                    if tx["currency"] == portfolio["base_currency"] and tx["account_id"] in account_ids
                    and tx["transaction_type"] in {"deposit", "buy"}]
    end = date(2026, 4, 15)
    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio, accounts, transactions, start_date=end, end_date=end, include_materialized_rows=True,
    )
    assert len(snapshots) == 1
    assert snapshots[0]["nav"] > 0


def test_only_actual_fx_pair_is_loaded_once_after_first_foreign_currency():
    calls = []
    details = {"fx-usd-hkd": fx_detail("fx-usd-hkd", "HKD", 7.8)}

    def load(instrument_id):
        calls.append(instrument_id)
        return details.get(instrument_id)

    detail_cache = valuation_fx.HistoricalInstrumentDetails(end_date=date(2026, 8, 3))
    arguments = dict(as_of_date=date(2026, 8, 3),
                     direct_instruments=valuation_fx.HistoricalFxInstruments(detail_cache, detail_loader=load),
                     instrument_detail_cache=detail_cache, instrument_detail_loader=load)
    assert valuation_fx.resolve_fx_rate_on(**arguments, base_currency="USD", quote_currency="USD")["rate"] == 1
    assert calls == []
    for pair in (("USD", "HKD"), ("HKD", "USD")):
        assert valuation_fx.resolve_fx_rate_on(**arguments, base_currency=pair[0], quote_currency=pair[1]) is not None
    assert calls == ["fx-usd-hkd"]


@pytest.mark.parametrize("fault", ["future_currency", "future_value", "duplicate_latest", "unregistered", "invalid_unit"])
@pytest.mark.parametrize("pair", [("USD", "HKD"), ("HKD", "USD"), ("HKD", "CNY")])
def test_actual_fx_discovery_keeps_full_history_catalog_fail_closed(fault, pair):
    details = {"fx-usd-hkd": fx_detail("fx-usd-hkd", "HKD", 7.8),
               "fx-usd-cny": fx_detail("fx-usd-cny", "CNY", 7.1)}
    source = details["fx-usd-hkd"]
    if fault == "future_currency":
        source["market_data"][-1]["currency"] = "EUR"
    elif fault == "future_value":
        source["market_data"][-1]["value"] = "nan"
    elif fault == "duplicate_latest":
        source["market_data"].append(deepcopy(source["market_data"][-1]))
    elif fault == "invalid_unit":
        source["market_data"][0]["price_unit"] = "per_unit"
    else:
        del details["fx-usd-hkd"]
    # The existing catalog checks the full history even when the daily window
    # ends earlier. Discovery must retain that stricter admission boundary;
    # dated quote lookup alone is not a replacement for catalog validation.
    cache = valuation_fx.HistoricalInstrumentDetails(end_date=date(2026, 8, 3))
    instruments = valuation_fx.HistoricalFxInstruments(cache, detail_loader=details.get)
    assert valuation_fx.resolve_fx_rate_on(
        as_of_date=date(2026, 8, 3), base_currency=pair[0], quote_currency=pair[1],
        direct_instruments=instruments, instrument_detail_cache=cache, instrument_detail_loader=details.get,
    ) is None


def test_initial_purchase_batch_preserves_evidence_and_exact_read_set():
    def transaction(instrument_id, transaction_type="buy", **values):
        return {
            "transaction_id": f"tx-{instrument_id}", "portfolio_id": "test",
            "instrument_id": instrument_id, "transaction_type": transaction_type,
            "trade_date": "2026-08-03", "position_effective_date": "2026-08-03",
            "quantity": 2.0, "gross_amount": 20.0, "currency": "USD", **values,
        }

    facts = [
        transaction("first"), transaction("second"), transaction("absent"),
        transaction("existing"), transaction("opening", "opening_balance"),
        transaction("delivery", noncash_delivery=True),
        transaction("short", _short_source_type="short_sell"),
        transaction("zero", quantity=0.0), transaction("mixed"),
        transaction("mixed", "sell", transaction_id="mixed-sell"),
        transaction("first", transaction_id="additional-initial", quantity=1.0, gross_amount=16.0),
        transaction("first", transaction_id="later-buy", position_effective_date="2026-08-04", gross_amount=90.0),
    ]
    source = {key: {"instrument_id": key, "market_data": []}
              for key in ("first", "second", "existing")}
    original_facts, original_source = deepcopy(facts), deepcopy(source)
    canonical = {"existing": source["existing"]}
    market_data.bind_initial_purchase_valuations(
        facts, canonical, instrument_detail_loader=source.get,
    )
    indexed = {"existing": source["existing"]}
    calls = []

    def batch(instrument_ids):
        calls.append(instrument_ids)
        return {key: source.get(key) for key in instrument_ids}

    def unexpected_single(_instrument_id):
        raise AssertionError("An initial purchase must reuse its batch, including absence.")

    for _ in range(2):
        market_data.bind_initial_purchase_valuations(
            facts, indexed, instrument_detail_loader=unexpected_single,
            instrument_details_loader=batch,
        )
    assert calls == [["first", "second", "absent"]]
    assert indexed == canonical
    assert indexed["first"]["_initial_purchase_valuation"]["value"] == 12.0
    assert indexed["absent"] is None
    assert source == original_source
    assert facts == original_facts


def test_daily_kernel_uses_one_initial_registry_batch(client, monkeypatch):
    original = performance.get_registry_instrument_detail
    batches = []

    def batch(instrument_ids):
        batches.append(tuple(instrument_ids))
        return {key: original(key) for key in instrument_ids}

    def unexpected_single(_instrument_id):
        raise AssertionError("Native-currency purchased securities must already be in the batch.")

    monkeypatch.setattr(performance, "get_registry_instrument_details", batch)
    monkeypatch.setattr(performance, "get_registry_instrument_detail", unexpected_single)
    portfolio = portfolio_store.get_portfolio("investment-studio")
    accounts = [account for account in portfolio_store.list_accounts("investment-studio")
                if account["currency"] == portfolio["base_currency"]]
    account_ids = {account["account_id"] for account in accounts}
    transactions = [tx for tx in portfolio_store.list_transactions("investment-studio")
                    if tx["currency"] == portfolio["base_currency"] and tx["account_id"] in account_ids
                    and tx["transaction_type"] in {"deposit", "buy"}]
    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio, accounts, transactions, start_date=date(2026, 4, 15),
        end_date=date(2026, 4, 15), include_materialized_rows=True,
    )
    assert len(batches) == 1
    assert len(batches[0]) > 1
    assert snapshots[0]["nav"] > 0
