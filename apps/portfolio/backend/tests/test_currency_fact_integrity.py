from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, inspect, select
from sqlalchemy.exc import IntegrityError

from portfolio_app.db.models import (
    TransactionIdentityRecordModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import holding_identity, portfolio_store
from portfolio_app.services.fact_currency import PortfolioFactCurrencyError
from portfolio_app.services.transaction_revisions import (
    AmendTransactionRevision,
    TransactionFactPayload,
    TransactionRevisionContext,
    append_transaction_revision_batch_unchecked,
)
from tests.store_fixture import TEST_PORTFOLIO_STORE


@pytest.mark.parametrize(
    ("collection", "index", "field_path", "bad_value", "message"),
    (
        ("portfolios", 0, ("base_currency",), None, "Portfolio 'portfolio-ops' base"),
        ("accounts", 0, ("currency",), "EUR", "Account 'cash-usd-main'"),
        ("transactions", 0, ("currency",), "", "Transaction 'txn-0001'"),
        (
            "transactions",
            2,
            ("instrument_ref", "currency"),
            "usd",
            "Transaction 'txn-0003' instrument reference",
        ),
    ),
)
def test_reset_store_rejects_missing_or_noncanonical_fact_currency_before_writing(
    collection: str,
    index: int,
    field_path: tuple[str, ...],
    bad_value: object,
    message: str,
) -> None:
    store = deepcopy(TEST_PORTFOLIO_STORE)
    target = store[collection][index]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value

    with pytest.raises(PortfolioFactCurrencyError, match=re.escape(message)):
        portfolio_store.reset_store(store)

    # Validation precedes the delete-and-replace unit of work.
    assert portfolio_store.get_portfolio("portfolio-ops") is not None


def test_revision_append_rejects_noncanonical_transaction_currency() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        transaction = session.scalar(
            select(TransactionRevisionRecordModel)
            .where(TransactionRevisionRecordModel.transaction_id == "txn-0001")
            .order_by(TransactionRevisionRecordModel.revision_number.desc())
            .limit(1)
        )
        assert transaction is not None
        assert transaction.transaction_type is not None
        assert transaction.trade_date is not None
        assert transaction.trade_time is not None
        assert transaction.trade_at is not None
        assert transaction.trade_timezone is not None
        assert transaction.trade_time_is_estimated is not None
        assert transaction.settlement_date is not None
        assert transaction.account_id is not None
        assert transaction.gross_amount is not None
        assert transaction.fees is not None
        assert transaction.taxes is not None
        facts = TransactionFactPayload(
            transaction_type=transaction.transaction_type,
            trade_date=transaction.trade_date,
            trade_time=transaction.trade_time,
            trade_at=(
                transaction.trade_at.replace(tzinfo=UTC)
                if transaction.trade_at.tzinfo is None
                else transaction.trade_at
            ),
            trade_timezone=transaction.trade_timezone,
            trade_time_is_estimated=transaction.trade_time_is_estimated,
            settlement_date=transaction.settlement_date,
            entitlement_date=transaction.entitlement_date,
            acquisition_date=transaction.acquisition_date,
            account_id=transaction.account_id,
            settlement_cash_account_id=transaction.settlement_cash_account_id,
            instrument_id=transaction.instrument_id,
            instrument_snapshot_json=transaction.instrument_snapshot_json,
            quantity=transaction.quantity,
            price=transaction.price,
            gross_amount=transaction.gross_amount,
            counter_amount=transaction.counter_amount,
            quoted_fx_rate=transaction.quoted_fx_rate,
            fees=transaction.fees,
            taxes=transaction.taxes,
            currency="EUR",
            consideration_basis=transaction.consideration_basis,
            numeric_scale_state=transaction.numeric_scale_state,
            quantity_input_scale=transaction.quantity_input_scale,
            price_input_scale=transaction.price_input_scale,
            gross_amount_input_scale=transaction.gross_amount_input_scale,
            counter_amount_input_scale=transaction.counter_amount_input_scale,
            quoted_fx_rate_input_scale=transaction.quoted_fx_rate_input_scale,
            fees_input_scale=transaction.fees_input_scale,
            taxes_input_scale=transaction.taxes_input_scale,
            transfer_scope=transaction.transfer_scope,
            transfer_object_type=transaction.transfer_object_type,
            transfer_group_id=transaction.transfer_group_id,
            counterparty_account_id=transaction.counterparty_account_id,
            note=transaction.note,
        )
        with pytest.raises(IntegrityError, match="supported_currency"):
            append_transaction_revision_batch_unchecked(
                session,
                portfolio_id="portfolio-ops",
                context=TransactionRevisionContext(
                    source_kind="manual",
                    change_reason="Attempt invalid currency revision",
                    actor_type="user",
                    actor_id="pm:test-manager",
                    actor_display_name="Test Portfolio Manager",
                    actor_source="client_asserted",
                ),
                mutations=(
                    AmendTransactionRevision(
                        transaction_id="txn-0001",
                        expected_revision_id=transaction.revision_id,
                        expected_revision_number=transaction.revision_number,
                        facts=facts,
                    ),
                ),
            )
        session.rollback()

    current = portfolio_store.get_transaction("portfolio-ops", "txn-0001")
    assert current is not None
    assert current["currency"] == "USD"


def test_transaction_ledger_check_constraint_names_match_runtime_metadata() -> None:
    inspector = inspect(get_engine())
    for model in (
        TransactionIdentityRecordModel,
        TransactionRevisionGroupRecordModel,
        TransactionRevisionRecordModel,
    ):
        expected_names = {
            str(constraint.name)
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        actual_names = {
            str(constraint["name"])
            for constraint in inspector.get_check_constraints(model.__tablename__)
        }
        assert actual_names == expected_names


def test_legacy_ledger_postings_route_is_removed(client) -> None:
    response = client.get("/api/portfolios/portfolio-ops/ledger-postings")

    assert response.status_code == 404


@pytest.mark.parametrize("bad_currency", (None, "", "usd", " USD ", "EUR"))
def test_cash_identity_boundary_requires_exact_canonical_currency(
    bad_currency: object,
) -> None:
    with pytest.raises(PortfolioFactCurrencyError):
        holding_identity.cash_holding_instrument_id(bad_currency)


def test_calculation_sources_do_not_restore_currency_fallbacks() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (backend_root / relative_path).read_text(encoding="utf-8")
        for relative_path in (
            "portfolio_app/services/portfolio_store.py",
            "portfolio_app/services/holding_identity.py",
            "portfolio_app/services/published_holdings.py",
            "portfolio_app/api/routes/accounts.py",
            "portfolio_app/api/routes/performance.py",
            "portfolio_app/api/routes/workspace.py",
        )
    )
    forbidden_patterns = (
        r'raw_portfolio\.get\("base_currency"\)\s+or\s+"USD"',
        r'raw_account\.get\("currency"\)\s+or\s+"USD"',
        r'raw_transaction\.get\("currency"\)\s+or\s+"USD"',
        r'copied_transaction\.get\("currency"\)\s+or\s+"USD"',
        r'holding\.get\("currency"\)\s+or\s+portfolio\.get\("base_currency"\)',
        r'portfolio\.get\("base_currency"\)\s+or\s+"USD"',
        r'(?:portfolio|resolved_portfolio)\.get\("base_currency",\s*"USD"\)',
        r'_normalized_currency\(\s*(?:transaction|position_lot|account|bucket)'
        r'\.get\("currency"\),\s*fallback=base_currency',
        r'base_currency:\s*str\s*=\s*"USD"',
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, source) is None, pattern


def test_explicit_new_portfolio_and_synthetic_cash_currency_are_legal() -> None:
    created = portfolio_store.create_portfolio(
        "Explicit currency policy",
        base_currency="CNY",
        operating_profile="standard_taxonomy",
    )
    assert created["base_currency"] == "CNY"

    with pytest.raises(PortfolioFactCurrencyError):
        portfolio_store.create_portfolio(
            "Missing currency",
            base_currency="",
            operating_profile="standard_taxonomy",
        )

    cash_instrument_id = holding_identity.cash_holding_instrument_id("HKD")
    assert cash_instrument_id == "cash:HKD"

    with pytest.raises(PortfolioFactCurrencyError):
        holding_identity.cash_holding_instrument_id("")


def test_portfolio_create_api_requires_an_explicit_base_currency(client) -> None:
    missing_currency = client.post(
        "/api/portfolios",
        json={"name": "No implicit currency"},
    )
    assert missing_currency.status_code == 422

    created = client.post(
        "/api/portfolios",
        json={
            "name": "Explicit CNY portfolio",
            "base_currency": "CNY",
            "operating_profile": "standard_taxonomy",
        },
    )
    assert created.status_code == 200, created.json()
    assert created.json()["base_currency"] == "CNY"
