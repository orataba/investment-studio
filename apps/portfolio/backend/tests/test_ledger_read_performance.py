from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.db.session import get_session_factory
from portfolio_app.services import ledger
from portfolio_ops_instrument_core import instrument_store as shared_store


EQUITY_ID = "equity-us-abbv"
FUND_ID = "fund-us-agg"


def _account(account_id: str = "broker") -> dict[str, object]:
    return {
        "account_id": account_id,
        "account_name": account_id,
        "account_type": "securities_account",
        "currency": "USD",
        "cost_basis_method": "fifo",
    }


def _buy(
    transaction_id: str,
    instrument_id: str,
    *,
    account_id: str = "broker",
    quantity: float = 10.0,
    gross_amount: float = 1000.0,
    instrument_type: str = "equity",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "portfolio_id": "portfolio",
        "transaction_type": "buy",
        "trade_date": "2026-04-01",
        "trade_at": "2026-04-01T10:00:00Z",
        "settlement_date": "2026-04-01",
        "account_id": account_id,
        "settlement_cash_account_id": None,
        "instrument_id": instrument_id,
        "instrument_ref": {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": instrument_type,
            "currency": currency,
            "identifiers": [],
        },
        "quantity": quantity,
        "gross_amount": gross_amount,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": currency,
        "created_at": "2026-04-01T10:00:00Z",
    }


def _equity_policy(*, valuation: list[str]) -> dict[str, list[str]]:
    return {
        "trading": ["last", "close"],
        "valuation": valuation,
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    }


def _upsert_equity_point(
    *,
    quote_basis: str,
    as_of_date: date,
    value: str,
    status: str = "complete",
) -> None:
    changed_count = shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=EQUITY_ID,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": quote_basis,
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "source_ref": f"test:{quote_basis}:{status}",
                "status": status,
            }
        ],
    )
    assert changed_count == 1


def _create_bond() -> str:
    created = shared_store.create_instrument(
        get_session_factory(),
        instrument_name="Canonical Test Bond",
        instrument_type="bond",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": "CANONICAL-BOND",
                "is_primary": True,
            }
        ],
    )
    instrument_id = str(created["instrument_id"])
    changed_count = shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "dirty_price",
                "as_of_date": date(2026, 4, 15),
                "value": "98.5",
                "currency": "USD",
                "source_ref": "test:dirty-price",
                "status": "complete",
            }
        ],
    )
    assert changed_count == 1
    return instrument_id


def test_valuation_quote_map_preserves_canonical_state_and_lineage() -> None:
    pricing = ledger._resolve_valuation_quote_map(
        {EQUITY_ID, FUND_ID},
        as_of_date=date(2026, 4, 15),
    )

    assert set(pricing) == {EQUITY_ID, FUND_ID}
    assert pricing[EQUITY_ID]["value"] == pytest.approx(206.47)
    assert pricing[FUND_ID]["value"] == pytest.approx(91.62)
    for instrument_id, quote in pricing.items():
        assert quote["instrument_id"] == instrument_id
        assert quote["role"] == "valuation"
        assert quote["quote_basis"] == "close"
        assert quote["currency"] == "USD"
        assert quote["resolution_status"] == "resolved"
        assert quote["source_status"] == "complete"
        assert quote["freshness_status"] == "current"
        assert quote["quote_series_id"]
        assert quote["observation_id"]
        assert quote["revision_id"]
        assert quote["payload_hash"]
        assert quote["calculation_dependency"]["fingerprint"]


def test_position_lot_filters_run_before_canonical_quote_resolution(monkeypatch) -> None:
    calls: list[set[str]] = []
    real_resolve = ledger._resolve_valuation_quote_map

    def recording_resolve(instrument_ids, *, as_of_date):
        calls.append(set(instrument_ids))
        return real_resolve(set(instrument_ids), as_of_date=as_of_date)

    monkeypatch.setattr(ledger, "_resolve_valuation_quote_map", recording_resolve)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    lots = ledger.build_position_lots(
        "portfolio",
        [_account("broker-a"), _account("broker-b")],
        [
            _buy("txn-a", EQUITY_ID, account_id="broker-a"),
            _buy("txn-b", FUND_ID, account_id="broker-b", instrument_type="fund"),
        ],
        account_id="broker-a",
        instrument_id=EQUITY_ID,
        status="open",
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{EQUITY_ID}]
    assert len(lots) == 1
    assert lots[0]["current_market_value"] == pytest.approx(2064.7)
    assert lots[0]["valuation_quote"]["revision_id"]


def test_portfolio_positions_reuse_quote_map_and_preserve_bond_scaling(monkeypatch) -> None:
    bond_id = _create_bond()
    calls: list[set[str]] = []
    real_resolve = ledger._resolve_valuation_quote_map

    def recording_resolve(instrument_ids, *, as_of_date):
        calls.append(set(instrument_ids))
        return real_resolve(set(instrument_ids), as_of_date=as_of_date)

    monkeypatch.setattr(ledger, "_resolve_valuation_quote_map", recording_resolve)
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    positions = ledger.build_portfolio_positions(
        "portfolio",
        [_account()],
        [
            _buy("txn-equity", EQUITY_ID, quantity=10.0),
            _buy(
                "txn-bond",
                bond_id,
                quantity=1000.0,
                gross_amount=985.0,
                instrument_type="bond",
            ),
        ],
        as_of_date=date(2026, 4, 15),
    )

    assert calls == [{EQUITY_ID, bond_id}]
    by_instrument = {position["instrument_id"]: position for position in positions}
    assert by_instrument[EQUITY_ID]["last_price"] == pytest.approx(206.47)
    assert by_instrument[EQUITY_ID]["market_value"] == pytest.approx(2064.7)
    assert by_instrument[bond_id]["last_price"] == pytest.approx(98.5)
    assert by_instrument[bond_id]["market_value"] == pytest.approx(985.0)
    assert by_instrument[bond_id]["valuation_quote"]["quote_basis"] == "dirty_price"


def test_account_workspace_reuses_canonical_quote_resolution(monkeypatch) -> None:
    pricing_calls: list[set[str]] = []
    quote_sessions: list[object] = []
    fx_sessions: list[object] = []
    corporate_action_calls: list[set[str]] = []
    real_resolve = ledger._resolve_valuation_quote_map_in_session
    real_fx_resolve = ledger.resolve_portfolio_fx_window_book_in_session

    def recording_resolve(session, instrument_ids, *, as_of_date):
        quote_sessions.append(session)
        pricing_calls.append(set(instrument_ids))
        return real_resolve(session, set(instrument_ids), as_of_date=as_of_date)

    def recording_fx_resolve(session, **kwargs):
        fx_sessions.append(session)
        return real_fx_resolve(session, **kwargs)

    def load_corporate_actions(instrument_ids, **_kwargs):
        corporate_action_calls.append(set(instrument_ids))
        return []

    monkeypatch.setattr(ledger, "_resolve_valuation_quote_map_in_session", recording_resolve)
    monkeypatch.setattr(
        ledger,
        "resolve_portfolio_fx_window_book_in_session",
        recording_fx_resolve,
    )
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", load_corporate_actions)

    workspace = ledger.build_account_workspace(
        "portfolio",
        [_account()],
        [_buy("txn-a", EQUITY_ID)],
        base_currency="USD",
        as_of_date=date(2026, 4, 15),
    )

    assert pricing_calls == [{EQUITY_ID}]
    assert len(quote_sessions) == len(fx_sessions) == 1
    assert quote_sessions[0] is fx_sessions[0]
    assert corporate_action_calls == [{EQUITY_ID}]
    assert workspace["positions"][0]["last_price"] == pytest.approx(206.47)
    assert workspace["positions"][0]["market_value"] == pytest.approx(2064.7)
    assert workspace["positions"][0]["valuation_quote"]["revision_id"]
    assert workspace["accounts"][0]["position_market_value"] == pytest.approx(2064.7)


def test_account_workspace_uses_canonical_fx_and_fails_closed_after_policy_limit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])
    account = {
        "account_id": "cash-hkd",
        "account_name": "HKD Cash",
        "account_type": "deposit_account",
        "currency": "HKD",
        "cost_basis_method": None,
    }
    transaction = {
        "transaction_id": "txn-hkd-cash",
        "portfolio_id": "portfolio",
        "transaction_type": "opening_balance",
        "trade_date": "2026-04-15",
        "settlement_date": "2026-04-15",
        "account_id": "cash-hkd",
        "settlement_cash_account_id": None,
        "instrument_id": None,
        "instrument_ref": None,
        "quantity": None,
        "gross_amount": 780.0,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "HKD",
        "created_at": "2026-04-15T09:00:00Z",
    }

    current = ledger.build_account_workspace(
        "portfolio",
        [account],
        [transaction],
        base_currency="USD",
        as_of_date=date(2026, 4, 15),
    )
    late = ledger.build_account_workspace(
        "portfolio",
        [account],
        [transaction],
        base_currency="USD",
        as_of_date=date(2026, 4, 21),
    )

    assert current["accounts"][0]["derived_cash_balance"] == pytest.approx(780.0)
    assert current["accounts"][0]["derived_cash_balance_base"] == pytest.approx(100.0)
    assert current["accounts"][0]["account_value_base"] == pytest.approx(100.0)
    assert current["accounts"][0]["valuation_missing_components"] == []

    assert late["accounts"][0]["derived_cash_balance"] == pytest.approx(780.0)
    assert late["accounts"][0]["derived_cash_balance_base"] is None
    assert late["accounts"][0]["account_value_base"] is None
    assert late["accounts"][0]["valuation_missing_components"] == ["cash_fx"]


def test_account_workspace_preserves_missing_price_propagation(monkeypatch) -> None:
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    workspace = ledger.build_account_workspace(
        "portfolio",
        [_account()],
        [_buy("txn-a", "missing-instrument")],
        base_currency="USD",
        as_of_date=date(2026, 4, 15),
    )

    assert workspace["positions"][0]["last_price"] is None
    assert workspace["positions"][0]["valuation_quote"] is None
    assert workspace["positions"][0]["market_value"] is None
    assert workspace["accounts"][0]["position_market_value"] is None
    assert workspace["accounts"][0]["account_value_base"] is None
    assert workspace["accounts"][0]["valuation_missing_components"] == ["position_price"]


def test_valuation_carries_one_locked_complete_series_with_lineage() -> None:
    _upsert_equity_point(
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="206.47",
    )
    pricing = ledger._resolve_valuation_quote_map(
        {EQUITY_ID},
        as_of_date=date(2026, 4, 16),
    )

    quote = pricing[EQUITY_ID]
    assert quote["value"] == pytest.approx(206.47)
    assert quote["quote_basis"] == "close"
    assert quote["as_of_date"] == date(2026, 4, 15)
    assert quote["source_status"] == "complete"
    assert quote["resolution_status"] == "resolved"
    assert quote["carry_forward"] is True
    assert quote["age_days"] == 1
    assert quote["reliability_status"] == "qualified"
    assert quote["reason_codes"] == ["carried_forward_observation"]
    assert quote["revision_id"]


def test_daily_market_freshness_allows_short_carry_then_fails_closed() -> None:
    within_policy = ledger._resolve_valuation_quote_map(
        {EQUITY_ID},
        as_of_date=date(2026, 4, 20),
    )[EQUITY_ID]
    beyond_policy = ledger._resolve_valuation_quote_map(
        {EQUITY_ID},
        as_of_date=date(2026, 4, 21),
    )[EQUITY_ID]

    assert within_policy["resolution_status"] == "resolved"
    assert within_policy["carry_forward"] is True
    assert within_policy["age_days"] == 5
    assert within_policy["canonical_instrument_type"] == "equity"
    assert within_policy["consumer_freshness_policy_type"] == "daily_market"
    assert within_policy["consumer_freshness_policy_version"] == "portfolio_quote_consumer.v2"
    assert within_policy["calculation_dependency"]["max_age_days"] == 5

    assert beyond_policy["resolution_status"] == "unavailable"
    assert beyond_policy["value"] is None
    assert beyond_policy["freshness_status"] == "late"
    assert beyond_policy["age_days"] == 6
    assert beyond_policy["reason_codes"] == [
        "late_observation",
        "freshness_limit_exceeded",
        "unknown_ingestion_time",
    ]


def test_periodic_fund_nav_uses_explicit_longer_publication_window() -> None:
    quote = ledger._resolve_valuation_quote_map(
        {FUND_ID},
        as_of_date=date(2026, 5, 15),
    )[FUND_ID]

    assert quote["resolution_status"] == "resolved"
    assert quote["carry_forward"] is True
    assert quote["age_days"] == 30
    assert quote["canonical_instrument_type"] == "fund"
    assert quote["consumer_freshness_policy_type"] == "periodic_fund_nav"
    assert quote["consumer_freshness_policy_version"] == "portfolio_quote_consumer.v2"
    assert quote["calculation_dependency"]["max_age_days"] == 45


def test_valuation_bad_current_revision_is_not_replaced_by_old_complete_value() -> None:
    _upsert_equity_point(
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="206.47",
        status="partial",
    )

    pricing = ledger._resolve_valuation_quote_map(
        {EQUITY_ID},
        as_of_date=date(2026, 4, 15),
    )

    quote = pricing[EQUITY_ID]
    assert quote["value"] is None
    assert quote["as_of_date"] == date(2026, 4, 15)
    assert quote["source_status"] == "partial"
    assert quote["status"] == "partial"
    assert quote["resolution_status"] == "unavailable"
    assert quote["reason_codes"] == ["partial_series"]
    assert quote["revision_id"]
    assert quote["revision_number"] == 2


def test_valuation_does_not_cross_policy_series_boundary() -> None:
    updated = shared_store.upsert_quote_selection_policy(
        get_session_factory(),
        instrument_id=EQUITY_ID,
        quote_selection_policy=_equity_policy(valuation=["last", "close"]),
    )
    assert updated is not None
    _upsert_equity_point(
        quote_basis="last",
        as_of_date=date(2026, 2, 28),
        value="190.00",
    )

    pricing = ledger._resolve_valuation_quote_map(
        {EQUITY_ID},
        as_of_date=date(2026, 4, 15),
    )

    quote = pricing[EQUITY_ID]
    assert quote["value"] is None
    assert quote["quote_basis"] == "last"
    assert quote["as_of_date"] == date(2026, 2, 28)
    assert quote["source_status"] == "complete"
    assert quote["freshness_status"] == "late"
    assert quote["age_days"] == 46
    assert quote["reason_codes"] == [
        "late_observation",
        "freshness_limit_exceeded",
    ]


def test_lot_valuation_never_stitches_quote_currency(monkeypatch) -> None:
    monkeypatch.setattr(ledger, "list_registry_corporate_actions", lambda *_args, **_kwargs: [])

    lots = ledger.build_position_lots(
        "portfolio",
        [_account()],
        [_buy("txn-a", EQUITY_ID, currency="CNY")],
        as_of_date=date(2026, 4, 15),
    )

    assert len(lots) == 1
    assert lots[0]["valuation_quote"]["resolution_status"] == "resolved"
    assert lots[0]["valuation_quote"]["currency"] == "USD"
    assert lots[0]["currency"] == "CNY"
    assert lots[0]["current_market_value"] is None
    assert lots[0]["unrealized_pnl"] is None
