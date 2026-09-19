from collections import OrderedDict
from copy import deepcopy
from datetime import date

import pytest
from investment_studio_instrument_core.db_models import Instrument
from sqlalchemy import select

from portfolio_app.db.session import get_session_factory
from portfolio_app.services import ledger, portfolio_store, source_cache, valuation_quotes


AS_OF = date(2026, 9, 8)


def detail(instrument_id="a", *, currency="USD", instrument_type="equity", value="100"):
    family, basis, unit = ("fx", "spot", "rate") if instrument_type == "fx" else ("price", "close", "per_unit")
    return {"instrument_id": instrument_id, "instrument_type": instrument_type,
            "currency": currency, "quote_selection_policy": {"valuation": [basis]},
            "market_data": [{"metric_family": family, "quote_basis": basis,
                             "as_of_date": AS_OF.isoformat(), "value": value,
                             "currency": currency, "status": "complete",
                             "price_unit": unit, "price_scale": 1.0}]}


@pytest.fixture()
def quotes(monkeypatch):
    state = {"generation": 1, "factory": object(), "details": {"a": detail()}, "reads": 0}
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
    monkeypatch.setattr(valuation_quotes, "get_session_factory", lambda: state["factory"])
    monkeypatch.setattr(valuation_quotes, "_source_generation", lambda ids: (
        state["generation"], tuple(iid for iid in ids if iid in state["details"]),
    ))

    def load(ids):
        state["reads"] += 1
        return {iid: deepcopy(state["details"].get(iid)) for iid in ids}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load)
    monkeypatch.setattr(portfolio_store, "list_option_delivery_links", lambda _pid: [])
    return state


def read(ids=None, *, as_of=AS_OF, transactions=None):
    return ledger._resolve_pricing_quote_map(ids or {"a"}, as_of_date=as_of, transactions=transactions)


def test_quote_reuse_checks_date_source_and_database_and_isolates_mutations(quotes):
    first = read()
    first["a"]["value"] = 999
    assert read()["a"]["value"] == 100
    assert quotes["reads"] == 1
    assert read(as_of=date(2026, 9, 7)) == {}
    assert quotes["reads"] == 2
    quotes["details"]["a"]["market_data"][0]["value"] = "110"
    quotes["generation"] += 1
    assert read()["a"]["value"] == 110
    quotes["factory"] = object()
    assert read()["a"]["value"] == 110
    assert quotes["reads"] == 4


def test_same_symbol_different_ids_and_newly_registered_instruments_do_not_share_quotes(quotes):
    quotes["details"]["a"]["identifiers"] = [{"identifier_type": "ticker", "value": "SAME"}]
    quotes["details"]["b"] = {**detail("b", value="200"), "identifiers": [{"identifier_type": "ticker", "value": "SAME"}]}
    assert {key: value["value"] for key, value in read({"a", "b"}).items()} == {"a": 100, "b": 200}
    assert read({"missing"}) == {}
    quotes["details"]["missing"] = detail("missing", value="300")
    assert read({"missing"})["missing"]["value"] == 300


def test_metadata_policy_changes_and_invalid_older_fx_observations_fail_closed(quotes):
    quotes["details"]["a"] = detail(currency="CNY", instrument_type="fx", value="7")
    assert read()["a"]["value"] == 7
    older = {**quotes["details"]["a"]["market_data"][0], "as_of_date": "2026-09-01", "currency": "EUR"}
    quotes["details"]["a"]["market_data"].append(older)
    quotes["generation"] += 1
    assert read() == {}
    quotes["details"]["a"]["market_data"].pop()
    quotes["details"]["a"]["quote_selection_policy"] = {"valuation": ["adjusted_close"]}
    quotes["generation"] += 1
    assert read() == {}


def test_source_changed_during_history_read_is_never_published_under_new_generation(quotes, monkeypatch):
    original = ledger.get_registry_instrument_details

    def changing(ids):
        result = original(ids)
        quotes["details"]["a"]["market_data"][0]["value"] = "120"
        quotes["generation"] = 2
        return result

    monkeypatch.setattr(ledger, "get_registry_instrument_details", changing)
    assert read()["a"]["value"] == 100
    assert source_cache._cache == {}
    assert read()["a"]["value"] == 120
    assert quotes["reads"] == 2


def test_purchase_transaction_basis_and_delivery_exclusions_are_always_live(quotes, monkeypatch):
    quotes["details"]["a"]["market_data"][0]["as_of_date"] = "2026-09-09"
    transaction = {"portfolio_id": "p", "transaction_id": "buy", "instrument_id": "a",
                   "transaction_type": "buy", "trade_date": AS_OF.isoformat(),
                   "settlement_date": AS_OF.isoformat(), "quantity": 10,
                   "gross_amount": 990, "currency": "USD"}
    assert read(transactions=[transaction])["a"]["value"] == 99
    transaction["gross_amount"] = 1010
    assert read(transactions=[transaction])["a"]["value"] == 101
    monkeypatch.setattr(portfolio_store, "list_option_delivery_links", lambda _pid: [{"stock_transaction_id": "buy"}])
    assert read(transactions=[transaction]) == {}
    assert quotes["reads"] == 1
    cached = next(iter(source_cache._cache.values())).value
    assert "_initial_purchase_valuation" not in cached["a"]["detail"]


def test_missing_or_invalid_official_series_keeps_original_initial_purchase_boundary(quotes):
    transaction = {"portfolio_id": "p", "transaction_id": "buy", "instrument_id": "a",
                   "transaction_type": "buy", "trade_date": AS_OF.isoformat(),
                   "quantity": 10, "gross_amount": 990, "currency": "USD"}
    quotes["details"]["a"]["market_data"][0]["value"] = "not-a-number"
    assert read(transactions=[transaction]) == {}


@pytest.mark.parametrize("watermark", ["market_data_updated_at", "calculation_inputs_updated_at"])
def test_persisted_quote_and_metadata_watermarks_invalidate_quote_reads(client, monkeypatch, watermark):
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
    with get_session_factory()() as session:
        instrument_id = session.scalar(select(Instrument.instrument_id).order_by(Instrument.instrument_id).limit(1))
    loads = []

    def load(ids):
        loads.append(tuple(ids))
        return {instrument_id: detail(instrument_id, value=str(100 + len(loads)))}

    monkeypatch.setattr(ledger, "get_registry_instrument_details", load)
    assert read({instrument_id})[instrument_id]["value"] == 101
    assert read({instrument_id})[instrument_id]["value"] == 101
    with get_session_factory()() as session:
        setattr(session.get(Instrument, instrument_id), watermark, "2026-09-08T10:00:00+00:00")
        session.commit()
    assert read({instrument_id})[instrument_id]["value"] == 102
