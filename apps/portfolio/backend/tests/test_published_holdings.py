from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from portfolio_app.services import published_holdings
from portfolio_app.services.fact_currency import PortfolioFactCurrencyError
from portfolio_app.services.holding_identity import (
    cash_holding_instrument_id,
    is_cash_holding_instrument_id,
)
from portfolio_app.services.published_holdings import (
    PublishedHoldingsIntegrityError,
    PublishedHoldingsUnavailableError,
)


pytestmark = pytest.mark.no_database


PUBLICATION_ID = UUID("00000000-0000-0000-0000-000000000101")
RUN_ID = UUID("00000000-0000-0000-0000-000000000102")
MANIFEST_ID = UUID("00000000-0000-0000-0000-000000000103")


def test_cash_holding_identity_requires_canonical_currency() -> None:
    assert cash_holding_instrument_id("HKD") == "cash:HKD"
    assert is_cash_holding_instrument_id(" CASH:hkd ") is True
    assert is_cash_holding_instrument_id("instrument-1") is False
    with pytest.raises(PortfolioFactCurrencyError):
        cash_holding_instrument_id("hkd")


def test_published_holdings_has_no_live_fact_or_market_data_fallback() -> None:
    source = Path(published_holdings.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "portfolio_app.services.portfolio_store",
        "portfolio_app.services.ledger",
        "portfolio_app.services.canonical_quotes",
        "portfolio_app.services.canonical_fx",
        "build_position_lots",
        "list_transactions",
        "resolve_quote",
        "resolve_fx",
    ):
        assert forbidden not in source, forbidden


def _metadata(**overrides):
    values = {
        "portfolio_id": "portfolio-1",
        "publication_id": PUBLICATION_ID,
        "run_id": RUN_ID,
        "manifest_id": MANIFEST_ID,
        "captured_generation": 7,
        "current_generation": 7,
        "pending_generation": None,
        "timezone_name": "UTC",
        "output_range_start": date(2026, 1, 1),
        "output_range_end": date(2026, 1, 2),
        "stale": False,
        "pending": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _holding(
    account_id: str,
    *,
    quantity: str,
    market_value: str,
    weight: str,
):
    return SimpleNamespace(
        account_id=account_id,
        instrument_id="instrument-1",
        currency="USD",
        quantity_exact=Decimal(quantity),
        measured_price=True,
        adopted_price_exact=Decimal("12.5"),
        measured_market_value=True,
        market_value_local_exact=Decimal(market_value),
        market_value_base_exact=Decimal(market_value),
        portfolio_weight=Decimal(weight),
        valuation_coverage_state="complete",
        valuation_coverage_reason_codes=(),
    )


def _lot(
    account_id: str,
    *,
    quantity: str,
    local_cost: str,
    base_cost: str,
):
    return SimpleNamespace(
        account_id=account_id,
        instrument_id="instrument-1",
        open_quantity_exact=Decimal(quantity),
        cost_basis_local_exact=Decimal(local_cost),
        measured_base_cost=True,
        cost_basis_base_exact=Decimal(base_cost),
    )


def _publication(*, holdings=(), lots=(), balances=()):
    return SimpleNamespace(
        metadata=_metadata(),
        requested_range_start=date(2026, 1, 2),
        requested_range_end=date(2026, 1, 2),
        snapshots=(
            SimpleNamespace(
                as_of_date=date(2026, 1, 2),
                base_currency="USD",
                closing_nav=Decimal("200.00000000"),
                nav_coverage_state="complete",
                nav_reason_codes=(),
            ),
        ),
        holdings=tuple(holdings),
        balances=tuple(balances),
        lots=tuple(lots),
        lot_dispositions=(),
        contributions=(),
    )


def test_projection_aggregates_only_exact_published_values_and_frozen_inputs() -> None:
    publication = _publication(
        holdings=(
            _holding("broker-a", quantity="4", market_value="50.000000001", weight="0.25"),
            _holding("broker-b", quantity="8", market_value="100.000000002", weight="0.50"),
        ),
        lots=(
            _lot("broker-a", quantity="4", local_cost="40.000000001", base_cost="40.000000001"),
            _lot("broker-b", quantity="8", local_cost="80.000000002", base_cost="80.000000002"),
        ),
        balances=(
            SimpleNamespace(
                account_id="cash-a",
                component_type="settled_cash",
                measured_base_amount=True,
                base_amount_exact=Decimal("49.999999997"),
                coverage_state="complete",
                reason_codes=(),
            ),
            SimpleNamespace(
                account_id="broker-a",
                component_type="pending_receivable",
                measured_base_amount=True,
                base_amount_exact=Decimal("5.000000001"),
                coverage_state="complete",
                reason_codes=(),
            ),
            SimpleNamespace(
                account_id="broker-a",
                component_type="pending_payable",
                measured_base_amount=True,
                base_amount_exact=Decimal("2.000000001"),
                coverage_state="complete",
                reason_codes=(),
            ),
        ),
    )

    statement = published_holdings._build_statement(
        publication,
        config={"base_currency": "USD"},
        instrument_rows=(
            {
                "instrument_id": "instrument-1",
                "instrument_name": "Frozen Fund",
                "instrument_type": "fund",
                "currency": "USD",
            },
        ),
        account_rows=(
            {
                "account_id": "cash-a",
                "account_name": "Cash A",
                "account_type": "deposit_account",
                "currency": "USD",
            },
            {
                "account_id": "broker-a",
                "account_name": "Broker A",
                "account_type": "securities_account",
                "currency": "USD",
            },
            {
                "account_id": "broker-b",
                "account_name": "Broker B",
                "account_type": "securities_account",
                "currency": "USD",
            },
        ),
    )

    assert len(statement.positions) == 1
    position = statement.positions[0]
    assert position.instrument_name == "Frozen Fund"
    assert position.quantity_exact == Decimal("12")
    assert position.market_value_base_exact == Decimal("150.000000003")
    assert position.cost_basis_base_exact == Decimal("120.000000003")
    assert position.portfolio_weight == Decimal("0.75")
    assert position.account_ids == ("broker-a", "broker-b")
    assert statement.position_market_value_base_exact == Decimal("150.000000003")
    assert statement.settled_cash_base_exact == Decimal("49.999999997")
    assert statement.pending_settlement_base_exact == Decimal("3.000000000")
    assert statement.total_nav_base == Decimal("200.00000000")
    assert all(
        not isinstance(value, float)
        for value in (
            position.quantity_exact,
            position.market_value_base_exact,
            position.cost_basis_base_exact,
            position.portfolio_weight,
            statement.settled_cash_base_exact,
        )
    )


def test_projection_rejects_lot_quantity_that_does_not_close_to_holding() -> None:
    publication = _publication(
        holdings=(
            _holding("broker-a", quantity="4", market_value="50", weight="0.25"),
        ),
        lots=(
            _lot("broker-a", quantity="3.999", local_cost="40", base_cost="40"),
        ),
    )

    with pytest.raises(PublishedHoldingsIntegrityError, match="lot quantity"):
        published_holdings._build_statement(
            publication,
            config={"base_currency": "USD"},
            instrument_rows=(
                {
                    "instrument_id": "instrument-1",
                    "instrument_name": "Frozen Fund",
                    "instrument_type": "fund",
                    "currency": "USD",
                },
            ),
            account_rows=(),
        )


class _SessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.parametrize(
    ("publication", "requested_date", "reason_code"),
    (
        (None, date(2026, 1, 2), "current_publication_missing"),
        (
            SimpleNamespace(metadata=_metadata(stale=True, pending=False)),
            date(2026, 1, 2),
            "current_publication_stale",
        ),
        (
            SimpleNamespace(metadata=_metadata(stale=False, pending=True)),
            date(2026, 1, 2),
            "newer_calculation_pending",
        ),
        (
            SimpleNamespace(metadata=_metadata(stale=False, pending=False)),
            date(2026, 1, 3),
            "requested_as_of_not_published",
        ),
    ),
)
def test_read_fails_closed_when_current_publication_is_not_usable(
    monkeypatch,
    publication,
    requested_date,
    reason_code,
) -> None:
    monkeypatch.setattr(
        published_holdings,
        "get_session_factory",
        lambda: lambda: _SessionContext(),
    )
    monkeypatch.setattr(
        published_holdings,
        "read_latest_current_portfolio_daily_publication",
        lambda *_args, **_kwargs: publication,
    )

    with pytest.raises(PublishedHoldingsUnavailableError) as captured:
        published_holdings.read_current_published_holdings(
            "portfolio-1",
            as_of_date=requested_date,
        )

    assert reason_code in captured.value.reason_codes


def test_read_rejects_pointer_change_after_frozen_input_projection(monkeypatch) -> None:
    publication = SimpleNamespace(metadata=_metadata(stale=False, pending=False))
    statement = SimpleNamespace()
    monkeypatch.setattr(
        published_holdings,
        "get_session_factory",
        lambda: lambda: _SessionContext(),
    )
    monkeypatch.setattr(
        published_holdings,
        "read_latest_current_portfolio_daily_publication",
        lambda *_args, **_kwargs: publication,
    )
    monkeypatch.setattr(
        published_holdings,
        "_frozen_inputs",
        lambda *_args, **_kwargs: ({}, (), ()),
    )
    monkeypatch.setattr(
        published_holdings,
        "_build_statement",
        lambda *_args, **_kwargs: statement,
    )
    monkeypatch.setattr(
        published_holdings,
        "read_current_portfolio_daily_metadata",
        lambda *_args, **_kwargs: _metadata(
            publication_id=UUID("00000000-0000-0000-0000-000000000999")
        ),
    )

    with pytest.raises(PublishedHoldingsUnavailableError) as captured:
        published_holdings.read_current_published_holdings(
            "portfolio-1",
            as_of_date=date(2026, 1, 2),
        )

    assert captured.value.reason_codes == ("publication_changed_retry",)


def test_read_rejects_invalidation_that_arrives_during_projection(monkeypatch) -> None:
    publication = SimpleNamespace(metadata=_metadata(stale=False, pending=False))
    monkeypatch.setattr(
        published_holdings,
        "get_session_factory",
        lambda: lambda: _SessionContext(),
    )
    monkeypatch.setattr(
        published_holdings,
        "read_latest_current_portfolio_daily_publication",
        lambda *_args, **_kwargs: publication,
    )
    monkeypatch.setattr(
        published_holdings,
        "_frozen_inputs",
        lambda *_args, **_kwargs: ({}, (), ()),
    )
    monkeypatch.setattr(
        published_holdings,
        "_build_statement",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        published_holdings,
        "read_current_portfolio_daily_metadata",
        lambda *_args, **_kwargs: _metadata(stale=True, pending=True),
    )

    with pytest.raises(PublishedHoldingsUnavailableError) as captured:
        published_holdings.read_current_published_holdings(
            "portfolio-1",
            as_of_date=date(2026, 1, 2),
        )

    assert captured.value.reason_codes == (
        "current_publication_stale",
        "newer_calculation_pending",
    )
