from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from decimal import Decimal
from threading import Barrier

from sqlalchemy import func, select

from portfolio_app.db.models import (
    TransactionCurrentModel,
    TransactionIdentityRecordModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.calculations.portfolio_daily import ledger_event_producer_events
from portfolio_app.calculations.portfolio_daily import capture_common
from portfolio_app.services import portfolio_store
from portfolio_app.services.transaction_command_validator import (
    TransactionCommandValidationError,
)
from portfolio_ops_instrument_core import instrument_store as shared_store


TEST_ACTOR = {
    "actor_type": "user",
    "actor_id": "pm:integrity-test",
    "display_name": "Integrity Test Manager",
    "actor_source": "client_asserted",
}


def _transaction_storage_counts() -> tuple[int, int, int, int]:
    session_factory = get_session_factory()
    with session_factory() as session:
        models = (
            TransactionIdentityRecordModel,
            TransactionRevisionRecordModel,
            TransactionRevisionGroupRecordModel,
            TransactionCurrentModel,
        )
        return tuple(
            int(
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.portfolio_id == "portfolio-ops")
                )
                or 0
            )
            for model in models
        )


def _create_deposit(note: str) -> dict[str, object]:
    return portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type="deposit",
        trade_date=date(2026, 4, 16),
        trade_time=None,
        settlement_date=date(2026, 4, 16),
        entitlement_date=None,
        acquisition_date=None,
        account_id="cash-usd-main",
        settlement_cash_account_id=None,
        instrument_id=None,
        instrument_ref=None,
        quantity=None,
        price=None,
        gross_amount="100.00000000",
        counter_amount=None,
        quoted_fx_rate=None,
        consideration_basis=None,
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=note,
        actor=TEST_ACTOR,
        change_reason=f"Create integrity-test deposit: {note}",
    )


def _create_abbv_sale(quantity: str, note: str) -> dict[str, object]:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    exact_quantity = Decimal(quantity)
    return portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type="sell",
        trade_date=date(2026, 4, 16),
        trade_time=None,
        settlement_date=date(2026, 4, 16),
        entitlement_date=None,
        acquisition_date=None,
        account_id="broker-us-core",
        settlement_cash_account_id="cash-usd-main",
        instrument_id="equity-us-abbv",
        instrument_ref=deepcopy(source["instrument_ref"]),
        quantity=quantity,
        price="200.000000000000",
        gross_amount=format(exact_quantity * Decimal("200.000000000000"), ".8f"),
        counter_amount=None,
        quoted_fx_rate=None,
        consideration_basis="source_reported",
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=note,
        actor=TEST_ACTOR,
        change_reason=f"Create integrity-test sale: {note}",
    )


def test_concurrent_position_sales_cannot_oversell() -> None:
    barrier = Barrier(2)

    def sell(index: int) -> tuple[str, object]:
        barrier.wait()
        try:
            return "created", _create_abbv_sale(
                "600.000000000000",
                f"concurrent sale {index}",
            )
        except TransactionCommandValidationError as error:
            return "rejected", error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(sell, range(2)))

    assert sorted(status for status, _result in results) == ["created", "rejected"]
    rejected_error = next(
        result for status, result in results if status == "rejected"
    )
    assert isinstance(rejected_error, TransactionCommandValidationError)
    assert rejected_error.reason_code == "oversell"
    assert "exceeds account position" in str(rejected_error)


def test_concurrent_transaction_ids_are_unique() -> None:
    barrier = Barrier(2)

    def deposit(index: int) -> dict[str, object]:
        barrier.wait()
        return _create_deposit(f"concurrent deposit {index}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(deposit, range(2)))

    assert len({str(record["transaction_id"]) for record in created}) == 2


def test_deleting_a_position_source_fact_cannot_invalidate_later_sales() -> None:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    counts_before = _transaction_storage_counts()
    try:
        portfolio_store.delete_transactions(
            "portfolio-ops",
            transaction_ids=["txn-0003"],
            expected_revisions={
                "txn-0003": (
                    str(source["revision_id"]),
                    int(source["revision_number"]),
                )
            },
            actor=TEST_ACTOR,
            change_reason="Attempt removal of a consumed source fact",
        )
    except TransactionCommandValidationError as error:
        assert error.reason_code == "position_not_found"
    else:
        raise AssertionError("consumed source fact deletion must be rejected")

    assert portfolio_store.get_transaction("portfolio-ops", "txn-0003") is not None
    assert _transaction_storage_counts() == counts_before


def test_exact_quantity_boundary_rejects_one_trillionth_share_oversell() -> None:
    before = portfolio_store.list_transactions("portfolio-ops")
    counts_before = _transaction_storage_counts()

    try:
        _create_abbv_sale(
            "880.000000000001",
            "exact one-trillionth oversell",
        )
    except TransactionCommandValidationError as error:
        assert error.reason_code == "oversell"
    else:
        raise AssertionError("an exact 0.000000000001-share oversell must be rejected")

    after = portfolio_store.list_transactions("portfolio-ops")
    assert [row["transaction_id"] for row in after] == [
        row["transaction_id"] for row in before
    ]
    assert _transaction_storage_counts() == counts_before


def test_exact_quantity_boundary_accepts_the_full_available_position() -> None:
    created = _create_abbv_sale(
        "880.000000000000",
        "exact full-position sale",
    )

    assert created["quantity"] == Decimal("880.000000000000")


def test_batch_rejects_sales_that_only_oversell_in_aggregate() -> None:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    counts_before = _transaction_storage_counts()

    records = [
        {
            "transaction_type": "sell",
            "trade_date": date(2026, 4, 16),
            "trade_time": trade_time,
            "settlement_date": date(2026, 4, 16),
            "entitlement_date": None,
            "acquisition_date": None,
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": deepcopy(source["instrument_ref"]),
            "quantity": "500.000000000000",
            "price": "200.000000000000",
            "gross_amount": "100000.00000000",
            "counter_amount": None,
            "quoted_fx_rate": None,
            "consideration_basis": "exact_quantity_price",
            "fees": "0.00000000",
            "taxes": "0.00000000",
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": f"aggregate oversell {trade_time}",
        }
        for trade_time in ("10:00", "11:00")
    ]
    try:
        portfolio_store.create_transactions(
            "portfolio-ops",
            records=records,
            actor=TEST_ACTOR,
            change_reason="Attempt aggregate batch oversell",
        )
    except TransactionCommandValidationError as error:
        assert error.reason_code == "oversell"
    else:
        raise AssertionError("an aggregate batch oversell must be rejected atomically")

    assert _transaction_storage_counts() == counts_before


def test_future_settling_position_transfer_is_replayed_through_settlement() -> None:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    counts_before = _transaction_storage_counts()
    transfer_group_id = "trf-future-settlement-oversell"
    common = {
        "trade_date": date(2026, 4, 16),
        "trade_time": "10:00",
        "settlement_date": date(2026, 4, 20),
        "entitlement_date": None,
        "acquisition_date": None,
        "settlement_cash_account_id": None,
        "instrument_id": "equity-us-abbv",
        "instrument_ref": deepcopy(source["instrument_ref"]),
        "quantity": "881.000000000000",
        "price": None,
        "gross_amount": "181709.44000000",
        "counter_amount": None,
        "quoted_fx_rate": None,
        "consideration_basis": None,
        "fees": "0.00000000",
        "taxes": "0.00000000",
        "currency": "USD",
        "transfer_scope": "internal_portfolio",
        "transfer_object_type": "position",
        "transfer_group_id": transfer_group_id,
        "note": "Future-settling position oversell",
    }
    records = [
        {
            **common,
            "transaction_type": "transfer_out",
            "account_id": "broker-us-core",
            "counterparty_account_id": "broker-us-income",
        },
        {
            **common,
            "transaction_type": "transfer_in",
            "account_id": "broker-us-income",
            "counterparty_account_id": "broker-us-core",
        },
    ]

    try:
        portfolio_store.create_transactions(
            "portfolio-ops",
            records=records,
            actor=TEST_ACTOR,
            change_reason="Attempt future-settling transfer oversell",
        )
    except TransactionCommandValidationError as error:
        assert error.reason_code == "oversell"
    else:
        raise AssertionError("future-settling position oversell must be rejected")

    assert _transaction_storage_counts() == counts_before


def test_non_base_currency_mutation_uses_local_book_without_market_fx(
    monkeypatch,
) -> None:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0017")
    assert source is not None

    def _forbid_market_fx(*_args, **_kwargs):
        raise AssertionError("prospective command validation must not build market FX")

    monkeypatch.setattr(
        ledger_event_producer_events,
        "_FxBook",
        _forbid_market_fx,
    )
    created = portfolio_store.create_transaction(
        portfolio_id="portfolio-ops",
        transaction_type="sell",
        trade_date=date(2026, 4, 16),
        trade_time="10:00",
        settlement_date=date(2026, 4, 17),
        entitlement_date=None,
        acquisition_date=None,
        account_id="broker-hk-core",
        settlement_cash_account_id="cash-hkd-main",
        instrument_id="fund-hk-2800",
        instrument_ref=deepcopy(source["instrument_ref"]),
        quantity="1.000000000000",
        price="20.000000000000",
        gross_amount="20.00000000",
        counter_amount=None,
        quoted_fx_rate=None,
        consideration_basis="exact_quantity_price",
        fees="0.00000000",
        taxes="0.00000000",
        currency="HKD",
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note="HKD local-book validation",
        actor=TEST_ACTOR,
        change_reason="Verify local-book command validation",
    )

    assert created["currency"] == "HKD"
    assert created["quantity"] == Decimal("1.000000000000")


def test_confirmed_split_is_applied_before_same_day_position_validation() -> None:
    action = shared_store.upsert_corporate_action_event(
        get_session_factory(),
        instrument_id="equity-us-abbv",
        action_type="share_split",
        announcement_date=date(2026, 4, 1),
        record_date=date(2026, 4, 15),
        effective_date=date(2026, 4, 16),
        payable_date=date(2026, 4, 16),
        new_units="2",
        old_units="1",
        source="issuer:test",
        status="confirmed",
        quantity_rounding="exact",
        quantity_precision=12,
        cost_basis_treatment="carry",
        external_event_id="abbv-split-test",
    )
    assert action is not None

    created = _create_abbv_sale(
        "1700.000000000000",
        "post-split exact sale",
    )

    assert created["quantity"] == Decimal("1700.000000000000")


def test_command_validation_reads_current_projection_not_cumulative_history(
    monkeypatch,
) -> None:
    def _forbid_historical_scan(*_args, **_kwargs):
        raise AssertionError("command validation must not scan cumulative revisions")

    monkeypatch.setattr(
        capture_common,
        "_latest_transaction_rows",
        _forbid_historical_scan,
    )

    created = _create_deposit("current-projection capture")
    assert created["transaction_type"] == "deposit"


def test_amending_a_consumed_source_fact_rolls_back_the_revision() -> None:
    source = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert source is not None
    counts_before = _transaction_storage_counts()

    try:
        portfolio_store.update_transaction(
            "portfolio-ops",
            "txn-0003",
            transaction_type="buy",
            trade_date=date(2026, 2, 10),
            trade_time="09:30",
            settlement_date=date(2026, 2, 12),
            entitlement_date=None,
            acquisition_date=None,
            account_id="broker-us-core",
            settlement_cash_account_id="cash-usd-main",
            instrument_id="equity-us-abbv",
            instrument_ref=deepcopy(source["instrument_ref"]),
            quantity="119.000000000000",
            price="206.470000000000",
            gross_amount="24569.93000000",
            counter_amount=None,
            quoted_fx_rate=None,
            consideration_basis="source_reported",
            fees="18.00000000",
            taxes="0.00000000",
            currency="USD",
            transfer_scope=None,
            transfer_object_type=None,
            transfer_group_id=None,
            counterparty_account_id=None,
            note="Attempt to shrink a consumed source fact",
            expected_revision_id=str(source["revision_id"]),
            expected_revision_number=int(source["revision_number"]),
            actor=TEST_ACTOR,
            change_reason="Attempt invalid source-fact amendment",
        )
    except TransactionCommandValidationError as error:
        assert error.reason_code == "oversell"
    else:
        raise AssertionError("history-breaking source-fact amendment must be rejected")

    current = portfolio_store.get_transaction("portfolio-ops", "txn-0003")
    assert current is not None
    assert current["revision_id"] == source["revision_id"]
    assert current["revision_number"] == source["revision_number"]
    assert current["quantity"] == Decimal("1000.000000000000")
    assert _transaction_storage_counts() == counts_before
