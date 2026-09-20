"""Cash FX keeps accounting boundaries while reading only its actual FX paths."""
from copy import deepcopy
from datetime import date

import pytest

from investment_studio_instrument_core.fx_contract import fx_instrument_identity
from investment_studio_instrument_core.fx_rates import latest_spot_point_from_instrument
from portfolio_app.services import ledger, market_data


def fx_detail(key, currency, rates):
    return {
        "instrument_id": key, "instrument_type": "fx", "currency": currency,
        "source_settings": {"market_calendar": "24/5"},
        "quote_selection_policy": {"valuation": ["spot"]},
        "market_data": [
            {"metric_family": "fx", "quote_basis": "spot", "as_of_date": f"2026-08-0{day}",
             "value": rate, "currency": currency, "price_unit": "rate",
             "price_scale": 1.0, "status": "complete"}
            for day, rate in rates.items()
        ],
    }


def cash_postings(currency):
    def posting(key, day, amount, **overrides):
        return {
            "transaction_id": key, "account_id": "cash", "currency": currency,
            "cash_amount_delta": amount, "trade_date": f"2026-08-0{day}",
            "settlement_date": f"2026-08-0{day}", "effective_date": f"2026-08-0{day}",
            "source_transaction_type": "deposit" if amount > 0 else "withdrawal",
            **overrides,
        }
    return [
        posting("opening", 3, 100),
        # Sale cash acquires its basis on trade day but only settles on day 5.
        posting("sale", 5, 30, trade_date="2026-08-04", source_transaction_type="sell"),
        posting("withdraw", 4, -20),
        posting("fee", 5, -5, source_transaction_type="expense", posting_role="fees"),
        posting("overdraw", 6, -150),
        posting("cover", 6, 60),
        posting("future", 7, -10),
    ]


@pytest.mark.parametrize("pair", [("USD", "HKD"), ("HKD", "USD"), ("HKD", "CNY")])
@pytest.mark.parametrize("fault", [None, "future_currency", "future_value", "duplicate_latest", "missing", "invalid_unit"])
def test_cash_fx_full_output_matches_catalog_and_dated_replay(monkeypatch, pair, fault):
    details = {
        "fx-usd-hkd": fx_detail("fx-usd-hkd", "HKD", {3: 7.8, 4: 7.9, 5: 8.0, 6: 8.1, 7: 8.2}),
        "fx-usd-cny": fx_detail("fx-usd-cny", "CNY", {3: 7.1, 4: 7.2, 5: 7.3, 6: 7.4, 7: 7.5}),
    }
    source = details["fx-usd-hkd"]["market_data"]
    if fault == "future_currency":
        source[-1]["currency"] = "EUR"
    elif fault == "future_value":
        source[-1]["value"] = "nan"
    elif fault == "duplicate_latest":
        source.append(deepcopy(source[-1]))
    elif fault == "invalid_unit":
        source[0]["price_unit"] = "per_unit"
    elif fault == "missing":
        del details["fx-usd-hkd"]
    original = deepcopy(details)
    # The old path admits pairs via the complete catalog, then resolves each
    # date with an ordinary detail dict, without the indexed quote path.
    eligible = {}
    for key, detail in details.items():
        if latest_spot_point_from_instrument(detail, detail["market_data"]) is not None:
            identity = fx_instrument_identity(key)
            eligible[(identity.base_currency, identity.quote_currency)] = key
    monkeypatch.setattr(ledger, "get_registry_instrument_detail", details.get)
    monkeypatch.setattr(ledger, "get_shared_fx_rates", lambda: pytest.fail("Global FX scan"))
    postings = cash_postings(pair[0])
    arguments = dict(transaction_ids={item["transaction_id"] for item in postings},
                     postings=postings, as_of_date=date(2026, 8, 6), base_currency=pair[1])
    expected = ledger.build_transaction_cash_fx_impacts(
        **arguments, direct_fx_instruments=eligible, instrument_detail_cache={},
    )
    actual = ledger.build_transaction_cash_fx_impacts(**arguments)
    assert actual == expected
    assert {item["transaction_id"] for item in actual} == {"withdraw", "fee", "overdraw", "cover"}
    if fault:
        assert all(item["realized_cash_fx_pnl_base"] is None for item in actual)
    elif pair == ("USD", "HKD"):
        withdrawal = next(item for item in actual if item["transaction_id"] == "withdraw")
        assert withdrawal["realized_cash_fx_pnl_base"] == pytest.approx(2.0)
        fee = next(item for item in actual if item["transaction_id"] == "fee")
        assert fee["historical_cost_basis_base"] == pytest.approx(5 * (80 * 7.8 + 30 * 7.9) / 110)
        assert fee["fair_value_base"] == pytest.approx(40)
    assert details == original


def test_cash_fx_loads_and_indexes_actual_pair_once_per_request(monkeypatch):
    detail = fx_detail("fx-usd-hkd", "HKD", {3: 7.8, 4: 7.9, 5: 8.0, 6: 8.1})
    reads, indexes = [], []
    original = market_data.resolve_quote_series

    def load(key):
        reads.append(key)
        assert key == "fx-usd-hkd", "Unrelated FX histories are not cash inputs."
        return deepcopy(detail)

    def index(*args, **kwargs):
        indexes.append(kwargs["end_date"])
        return original(*args, **kwargs)

    monkeypatch.setattr(ledger, "get_registry_instrument_detail", load)
    monkeypatch.setattr(ledger, "get_shared_fx_rates", lambda: pytest.fail("Global FX scan"))
    monkeypatch.setattr(market_data, "resolve_quote_series", index)
    postings = cash_postings("USD")
    arguments = dict(transaction_ids={item["transaction_id"] for item in postings},
                     postings=postings, as_of_date=date(2026, 8, 6), base_currency="HKD")
    before = ledger.build_transaction_cash_fx_impacts(**arguments)
    assert reads == ["fx-usd-hkd"]
    assert indexes == [date(2026, 8, 6)]
    # Source facts are read again in the next request, without a TTL.
    detail["market_data"][1]["value"] = 8.5
    after = ledger.build_transaction_cash_fx_impacts(**arguments)
    assert after != before
    assert reads == ["fx-usd-hkd", "fx-usd-hkd"]
    assert indexes == [date(2026, 8, 6), date(2026, 8, 6)]


@pytest.mark.parametrize("pair", [("USD", "HKD"), ("HKD", "USD"), ("HKD", "CNY"), ("USD", "USD")])
@pytest.mark.parametrize("future_fault", [False, True])
def test_account_fx_keeps_settled_pending_transferred_basis_and_selection(monkeypatch, pair, future_fault):
    details = {
        "fx-usd-hkd": fx_detail("fx-usd-hkd", "HKD", {3: 7.8, 4: 7.9, 5: 8.0, 6: 8.1}),
        "fx-usd-cny": fx_detail("fx-usd-cny", "CNY", {3: 7.1, 4: 7.2, 5: 7.3, 6: 7.4}),
    }
    if future_fault:
        details["fx-usd-hkd"]["market_data"][-1]["value"] = "nan"
    original = deepcopy(details)
    eligible = {}
    for key, detail in details.items():
        if latest_spot_point_from_instrument(detail, detail["market_data"]) is not None:
            identity = fx_instrument_identity(key)
            eligible[(identity.base_currency, identity.quote_currency)] = key
    monkeypatch.setattr(ledger, "get_registry_instrument_detail", details.get)
    monkeypatch.setattr(ledger, "get_shared_fx_rates", lambda: pytest.fail("Global FX scan"))
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])
    accounts = [
        {"account_id": key, "account_name": key, "account_type": "deposit_account", "currency": pair[0]}
        for key in ("cash-a", "cash-b")
    ]

    def transaction(key, day, kind, amount, **overrides):
        return {
            "transaction_id": key, "portfolio_id": "portfolio", "account_id": "cash-a",
            "transaction_type": kind, "currency": pair[0], "gross_amount": amount,
            "trade_date": f"2026-08-0{day}", "trade_at": f"2026-08-0{day}T09:00:00Z",
            "settlement_date": f"2026-08-0{day}", "created_at": f"2026-08-0{day}T10:00:00Z",
            "fees": 0, "taxes": 0, **overrides,
        }
    transactions = [
        transaction("opening", 3, "opening_balance", 100),
        transaction("transfer-out", 4, "transfer_out", 30, transfer_group_id="move",
                    transfer_object_type="cash", counterparty_account_id="cash-b"),
        transaction("transfer-in", 4, "transfer_in", 30, transfer_group_id="move",
                    transfer_object_type="cash", counterparty_account_id="cash-a", account_id="cash-b"),
        transaction("pending-interest", 4, "interest", 10, settlement_date="2026-08-06"),
        transaction("withdraw", 5, "withdrawal", 20, fees=1, taxes=1),
        transaction("future", 6, "withdrawal", 10),
    ]
    for sequence, row in enumerate(transactions, start=1):
        row["transaction_sequence"] = sequence
    arguments = dict(as_of_date=date(2026, 8, 5), base_currency=pair[1])
    for selected in (None, "cash-b"):
        expected = ledger.build_account_workspace(
            "portfolio", accounts, transactions, **arguments, selected_account_id=selected,
            direct_fx_instruments=eligible, instrument_detail_cache={},
        )
        actual = ledger.build_account_workspace(
            "portfolio", accounts, transactions, **arguments, selected_account_id=selected,
        )
        assert actual == expected
        rows = {row["account"]["account_id"]: row for row in actual["accounts"]}
        assert rows["cash-a"]["pending_settlement"] == 10
        assert rows["cash-b"]["derived_cash_balance"] == 30
        if not future_fault and pair == ("USD", "HKD"):
            assert rows["cash-b"]["settled_cash_cost_basis_base"] == pytest.approx(30 * 7.8)
            assert rows["cash-b"]["settled_cash_unrealized_fx_pnl_base"] == pytest.approx(30 * (8.0 - 7.8))
            assert rows["cash-a"]["pending_settlement_cost_basis_base"] == pytest.approx(10 * 7.9)
            assert rows["cash-a"]["pending_settlement_unrealized_fx_pnl_base"] == pytest.approx(1)
        if future_fault and pair[0] != pair[1]:
            assert rows["cash-b"]["account_value_base"] is None
            assert rows["cash-a"]["pending_settlement_base"] is None
    assert details == original
