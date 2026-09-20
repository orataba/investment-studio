"""Financial boundaries that are not established by production payload hashes."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date

import pytest
from investment_studio_instrument_core import instrument_store
from investment_studio_instrument_core.db_models import Instrument
from sqlalchemy import select

from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, ledger, portfolio_store, source_cache, valuation_quotes


AS_OF = date(2026, 9, 8)


@pytest.fixture()
def quote_sources(monkeypatch):
    detail = {
        "instrument_id": "equity", "instrument_type": "equity", "currency": "USD",
        "exchange_code": "XNYS", "source_settings": {"market_calendar": "XNYS"},
        "quote_selection_policy": {"valuation": ["close"]},
        "market_data": [{"metric_family": "price", "quote_basis": "close",
                         "as_of_date": "2026-09-08", "value": "100", "currency": "USD",
                         "price_unit": "per_unit", "price_scale": "1", "status": "complete"}],
    }
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
    monkeypatch.setattr(valuation_quotes, "get_session_factory", lambda: "quote-contract-test")
    monkeypatch.setattr(valuation_quotes, "_source_generation", lambda ids: tuple(ids))
    monkeypatch.setattr(ledger, "get_registry_instrument_details", lambda ids: {
        iid: deepcopy(detail) for iid in ids
    })
    return detail


@pytest.mark.parametrize("case,expected", [
    ("official", 100), ("fund_nav", 100), ("adjusted_valuation", None),
    ("currency_mismatch", None), ("invalid_scale", None),
    ("ambiguous_series", None), ("future_quote", None),
])
def test_cached_and_preloaded_valuation_preserve_identity_and_cutoff(quote_sources, case, expected):
    detail = quote_sources
    point = detail["market_data"][0]
    if case == "fund_nav":
        detail.update(instrument_type="public_fund", exchange_code=None)
        detail["quote_selection_policy"] = {"valuation": ["official_nav"]}
        point.update(metric_family="nav", quote_basis="official_nav")
    elif case == "adjusted_valuation":
        detail["quote_selection_policy"] = {"valuation": ["adjusted_close"]}
        point["quote_basis"] = "adjusted_close"
    elif case == "currency_mismatch":
        point["currency"] = "EUR"
    elif case == "invalid_scale":
        point["price_scale"] = "100"
    elif case == "ambiguous_series":
        detail["market_data"].append(deepcopy(point))
    elif case == "future_quote":
        point["as_of_date"] = "2026-09-09"
    original = deepcopy(detail)
    direct = ledger._resolve_pricing_quote_map({"equity"}, as_of_date=AS_OF,
                                               instrument_detail_cache={"equity": deepcopy(detail)})
    for _ in range(2):
        cached = ledger._resolve_pricing_quote_map({"equity"}, as_of_date=AS_OF)
        assert cached == direct
        assert cached.get("equity", {}).get("value") == expected
    assert detail == original


def test_shared_quote_reuse_never_crosses_portfolio_purchase_evidence(quote_sources, monkeypatch):
    quote_sources["market_data"][0]["as_of_date"] = "2026-09-09"
    monkeypatch.setattr(portfolio_store, "list_option_delivery_links", lambda _pid: [])

    def price(portfolio_id, gross):
        facts = [{"portfolio_id": portfolio_id, "transaction_id": f"{portfolio_id}-buy",
                  "instrument_id": "equity", "transaction_type": "buy",
                  "trade_date": AS_OF.isoformat(), "settlement_date": AS_OF.isoformat(),
                  "quantity": 10, "gross_amount": gross, "currency": "USD"}]
        return ledger._resolve_pricing_quote_map({"equity"}, as_of_date=AS_OF, transactions=facts)["equity"]

    first, second = price("one", 900), price("two", 1100)
    assert first["value"] == 90
    assert second["value"] == 110
    assert price("one", 900) == first
    assert ledger._resolve_pricing_quote_map({"equity"}, as_of_date=AS_OF) == {}


def test_later_committed_configuration_cannot_hide_below_another_asset_watermark(monkeypatch):
    """A writer that began earlier can commit after a newer timestamp writer."""
    portfolio_id = "investment-studio"
    assert daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    factory = get_session_factory()
    with factory() as session:
        relevant_ids = daily_snapshots._portfolio_source_instrument_ids(session, portfolio_id)
        assets = list(session.scalars(select(Instrument).where(
            Instrument.instrument_id.in_(relevant_ids), Instrument.instrument_type.in_(("equity", "etf")),
        ).order_by(Instrument.instrument_id)))
        assert len(assets) >= 2
        earlier_writer, later_writer = assets[:2]
        earlier_writer.calculation_inputs_updated_at = "2099-01-01T00:00:00.000000Z"
        later_writer.calculation_inputs_updated_at = "2099-01-01T00:00:02.000000Z"
        instrument_id = earlier_writer.instrument_id
        policy = deepcopy(earlier_writer.quote_selection_policy_json)
        policy["valuation"] = list(reversed(policy["valuation"]))
        session.commit()
        before = daily_snapshots._snapshot_source_generation(session, portfolio_id)
    # The earlier process reserved its local timestamp before the other writer.
    # Global ordering must still make this newly committed input visible.
    monkeypatch.setattr(instrument_store, "_utcnow_iso", lambda: "2099-01-01T00:00:01.000000Z")
    instrument_store.upsert_quote_selection_policy(factory, instrument_id=instrument_id,
                                                  quote_selection_policy=policy)
    with factory() as session:
        after = daily_snapshots._snapshot_source_generation(session, portfolio_id)
    assert after != before
