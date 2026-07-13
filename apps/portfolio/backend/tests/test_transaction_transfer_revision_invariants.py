from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from portfolio_app.db.models import (
    TransactionCurrentModel,
    TransactionIdentityRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import portfolio_store
from portfolio_app.services.transaction_revisions import (
    AmendTransactionRevision,
    CreateTransactionRevision,
    DeleteTransactionRevision,
    TransactionFactPayload,
    TransactionRevisionPayloadError,
    TransactionRevisionContext,
    append_transaction_revision_batch,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
TEST_ACTOR = {
    "actor_type": "user",
    "actor_id": "pm:transfer-invariant-test",
    "display_name": "Transfer Invariant Test",
    "actor_source": "client_asserted",
}


def _cash_transfer_leg(
    *,
    transaction_type: str,
    account_id: str,
    counterparty_account_id: str,
    transfer_group_id: str,
    gross_amount: str = "100.00000000",
    settlement_date: date = date(2026, 4, 16),
) -> dict[str, object]:
    return {
        "transaction_type": transaction_type,
        "trade_date": date(2026, 4, 16),
        "trade_time": "09:30",
        "settlement_date": settlement_date,
        "entitlement_date": None,
        "acquisition_date": None,
        "account_id": account_id,
        "settlement_cash_account_id": None,
        "instrument_id": None,
        "instrument_ref": None,
        "quantity": None,
        "price": None,
        "gross_amount": gross_amount,
        "counter_amount": None,
        "fx_rate": None,
        "fees": "0.00000000",
        "taxes": "0.00000000",
        "currency": "USD",
        "transfer_scope": "internal_portfolio",
        "transfer_object_type": "cash",
        "transfer_group_id": transfer_group_id,
        "counterparty_account_id": counterparty_account_id,
        "note": "Transfer invariant test",
    }


def _create_transfer_records(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    return portfolio_store.create_transactions(
        portfolio_id="portfolio-ops",
        records=records,
        actor=TEST_ACTOR,
        change_reason="Exercise internal transfer invariants",
    )


def _assert_transfer_group_absent(transfer_group_id: str) -> None:
    with get_session_factory()() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TransactionRevisionRecordModel)
                .where(
                    TransactionRevisionRecordModel.portfolio_id == "portfolio-ops",
                    TransactionRevisionRecordModel.transfer_group_id
                    == transfer_group_id,
                )
            )
            == 0
        )


def test_create_transactions_rejects_single_internal_transfer_leg() -> None:
    transfer_group_id = "transfer-single-leg-test"

    with pytest.raises(
        ValueError,
        match="requires exactly one in leg and one out leg",
    ):
        _create_transfer_records(
            [
                _cash_transfer_leg(
                    transaction_type="transfer_out",
                    account_id="cash-usd-main",
                    counterparty_account_id="cash-usd-reserve",
                    transfer_group_id=transfer_group_id,
                )
            ]
        )

    _assert_transfer_group_absent(transfer_group_id)


def test_revision_primitive_rejects_single_transfer_create_without_store_guard() -> None:
    context = TransactionRevisionContext(
        source_kind="system",
        change_reason="Verify canonical transfer create enforcement",
        actor_type="service",
        actor_id="test:transfer-invariants",
        actor_display_name="Transfer Invariant Test",
        actor_source="trusted_service",
        recorded_at=datetime(2026, 4, 16, 9, 30, tzinfo=UTC),
    )
    facts = TransactionFactPayload(
        transaction_type="transfer_out",
        trade_date=date(2026, 4, 16),
        trade_time=time(9, 30),
        trade_at=context.recorded_at,
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 4, 16),
        account_id="cash-usd-main",
        gross_amount="100.00000000",
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope="internal_portfolio",
        transfer_object_type="cash",
        transfer_group_id="transfer-direct-single-leg-test",
        counterparty_account_id="cash-usd-reserve",
    )

    with get_session_factory()() as session:
        with pytest.raises(
            TransactionRevisionPayloadError,
            match="requires exactly one in leg and one out leg",
        ):
            append_transaction_revision_batch(
                session,
                portfolio_id="portfolio-ops",
                context=context,
                mutations=(
                    CreateTransactionRevision(
                        transaction_id="transaction-direct-single-leg-test",
                        facts=facts,
                        created_at=context.recorded_at,
                    ),
                ),
            )
        session.rollback()


def test_create_transactions_rejects_nonreciprocal_transfer_accounts() -> None:
    transfer_group_id = "transfer-nonreciprocal-test"

    with pytest.raises(ValueError, match="account links are not reciprocal"):
        _create_transfer_records(
            [
                _cash_transfer_leg(
                    transaction_type="transfer_out",
                    account_id="cash-usd-main",
                    counterparty_account_id="cash-usd-reserve",
                    transfer_group_id=transfer_group_id,
                ),
                _cash_transfer_leg(
                    transaction_type="transfer_in",
                    account_id="cash-usd-reserve",
                    counterparty_account_id="cash-usd-reserve",
                    transfer_group_id=transfer_group_id,
                ),
            ]
        )

    _assert_transfer_group_absent(transfer_group_id)


def test_create_transactions_rejects_inconsistent_transfer_leg_fields() -> None:
    transfer_group_id = "transfer-inconsistent-fields-test"

    with pytest.raises(ValueError, match=r"inconsistent legs: gross_amount"):
        _create_transfer_records(
            [
                _cash_transfer_leg(
                    transaction_type="transfer_out",
                    account_id="cash-usd-main",
                    counterparty_account_id="cash-usd-reserve",
                    transfer_group_id=transfer_group_id,
                    gross_amount="100.00000000",
                ),
                _cash_transfer_leg(
                    transaction_type="transfer_in",
                    account_id="cash-usd-reserve",
                    counterparty_account_id="cash-usd-main",
                    transfer_group_id=transfer_group_id,
                    gross_amount="101.00000000",
                ),
            ]
        )

    _assert_transfer_group_absent(transfer_group_id)


def test_transfer_group_history_cannot_be_reused_after_delete(client) -> None:
    transfer_group_id = "transfer-history-reuse-test"
    request = {
        "trade_date": "2026-04-16",
        "trade_time": "10:15",
        "settlement_date": "2026-04-16",
        "from_account_id": "cash-usd-main",
        "to_account_id": "cash-usd-reserve",
        "transfer_object_type": "cash",
        "gross_amount": "250.00000000",
        "transfer_group_id": transfer_group_id,
        "note": "History reuse sentinel",
        "actor": TEST_ACTOR,
        "change_reason": "Create the transfer history sentinel",
    }

    created_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json=request,
    )
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    assert created["transfer_group_id"] == transfer_group_id

    delete_target = created["transactions"][0]
    deleted_response = client.request(
        "DELETE",
        (
            "/api/portfolios/portfolio-ops/transactions/"
            f"{delete_target['transaction_id']}"
        ),
        json={
            "expected_revision_id": delete_target["revision_id"],
            "expected_revision_number": delete_target["revision_number"],
            "actor": TEST_ACTOR,
            "change_reason": "Delete both transfer legs atomically",
        },
    )
    assert deleted_response.status_code == 200, deleted_response.text
    assert deleted_response.json()["deleted_count"] == 2

    reused_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json={
            **request,
            "change_reason": "Attempt to reuse deleted transfer history",
        },
    )
    assert reused_response.status_code == 409, reused_response.text
    assert "already exists in ledger history" in reused_response.text

    with get_session_factory()() as session:
        historical_revisions = session.scalar(
            select(func.count())
            .select_from(TransactionRevisionRecordModel)
            .where(
                TransactionRevisionRecordModel.portfolio_id == "portfolio-ops",
                TransactionRevisionRecordModel.transfer_group_id
                == transfer_group_id,
            )
        )
        assert historical_revisions == 2


def test_revision_primitive_rejects_transfer_amend_and_single_leg_delete() -> None:
    transfer_group_id = "transfer-direct-mutation-guard-test"
    created = _create_transfer_records(
        [
            _cash_transfer_leg(
                transaction_type="transfer_out",
                account_id="cash-usd-main",
                counterparty_account_id="cash-usd-reserve",
                transfer_group_id=transfer_group_id,
            ),
            _cash_transfer_leg(
                transaction_type="transfer_in",
                account_id="cash-usd-reserve",
                counterparty_account_id="cash-usd-main",
                transfer_group_id=transfer_group_id,
            ),
        ]
    )
    outbound_id = str(
        next(
            record["transaction_id"]
            for record in created
            if record["transaction_type"] == "transfer_out"
        )
    )
    context = TransactionRevisionContext(
        source_kind="system",
        change_reason="Verify canonical transfer mutation enforcement",
        actor_type="service",
        actor_id="test:transfer-invariants",
        actor_display_name="Transfer Invariant Test",
        actor_source="trusted_service",
        recorded_at=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
    )

    with get_session_factory()() as session:
        current = session.get(TransactionCurrentModel, outbound_id)
        assert current is not None
        current_trade_at = current.trade_at
        if current_trade_at.tzinfo is None or current_trade_at.utcoffset() is None:
            # SQLite does not round-trip timezone metadata for DateTime even
            # when the model declares timezone=True.  Restore the persisted
            # UTC contract explicitly before exercising the revision primitive.
            current_trade_at = current_trade_at.replace(tzinfo=UTC)
        facts = TransactionFactPayload(
            transaction_type=str(current.transaction_type),
            trade_date=current.trade_date,
            trade_time=current.trade_time,
            trade_at=current_trade_at,
            trade_timezone=current.trade_timezone,
            trade_time_is_estimated=current.trade_time_is_estimated,
            settlement_date=current.settlement_date,
            account_id=current.account_id,
            gross_amount=current.gross_amount,
            fees=current.fees,
            taxes=current.taxes,
            currency=current.currency,
            transfer_scope=current.transfer_scope,
            transfer_object_type=current.transfer_object_type,
            transfer_group_id=current.transfer_group_id,
            counterparty_account_id=current.counterparty_account_id,
            note="Attempted direct amendment",
        )
        with pytest.raises(
            TransactionRevisionPayloadError,
            match="cannot be amended",
        ):
            append_transaction_revision_batch(
                session,
                portfolio_id="portfolio-ops",
                context=context,
                mutations=(
                    AmendTransactionRevision(
                        transaction_id=outbound_id,
                        expected_revision_id=current.current_revision_id,
                        expected_revision_number=current.current_revision_number,
                        facts=facts,
                    ),
                ),
            )
        session.rollback()

    with get_session_factory()() as session:
        current = session.get(TransactionCurrentModel, outbound_id)
        assert current is not None
        with pytest.raises(
            TransactionRevisionPayloadError,
            match="must be deleted atomically",
        ):
            append_transaction_revision_batch(
                session,
                portfolio_id="portfolio-ops",
                context=context,
                mutations=(
                    DeleteTransactionRevision(
                        transaction_id=outbound_id,
                        expected_revision_id=current.current_revision_id,
                        expected_revision_number=current.current_revision_number,
                    ),
                ),
            )
        session.rollback()


def test_transfer_settlement_cash_account_is_rejected_by_api(client) -> None:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "transfer_out",
            "trade_date": "2026-04-16",
            "trade_time": "09:30",
            "settlement_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": "cash-usd-reserve",
            "gross_amount": "100.00000000",
            "currency": "USD",
            "transfer_scope": "internal_portfolio",
            "transfer_object_type": "cash",
            "transfer_group_id": "transfer-api-settlement-cash-test",
            "counterparty_account_id": "cash-usd-reserve",
            "actor": TEST_ACTOR,
            "change_reason": "Verify transfer settlement cash rejection",
        },
    )

    assert response.status_code == 422
    assert "must not carry settlement_cash_account_id" in response.text


def test_transfer_settlement_cash_account_is_rejected_by_database() -> None:
    transaction_id = "transaction-db-settlement-cash-test"
    paired_transaction_id = "transaction-db-settlement-cash-pair-test"
    context = TransactionRevisionContext(
        source_kind="system",
        change_reason="Verify the database transfer envelope",
        actor_type="service",
        actor_id="test:transfer-invariants",
        actor_display_name="Transfer Invariant Test",
        actor_source="trusted_service",
        recorded_at=datetime(2026, 4, 16, 9, 30, tzinfo=UTC),
    )
    facts = TransactionFactPayload(
        transaction_type="transfer_out",
        trade_date=date(2026, 4, 16),
        trade_time=time(9, 30),
        trade_at=datetime(2026, 4, 16, 9, 30, tzinfo=UTC),
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 4, 16),
        account_id="cash-usd-main",
        settlement_cash_account_id="cash-usd-reserve",
        gross_amount="100.00000000",
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope="internal_portfolio",
        transfer_object_type="cash",
        transfer_group_id="transfer-db-settlement-cash-test",
        counterparty_account_id="cash-usd-reserve",
    )
    paired_facts = replace(
        facts,
        transaction_type="transfer_in",
        account_id="cash-usd-reserve",
        counterparty_account_id="cash-usd-main",
    )

    with get_session_factory()() as session:
        with pytest.raises(IntegrityError, match="transfer_fields"):
            append_transaction_revision_batch(
                session,
                portfolio_id="portfolio-ops",
                context=context,
                mutations=(
                    CreateTransactionRevision(
                        transaction_id=transaction_id,
                        facts=facts,
                        created_at=context.recorded_at,
                    ),
                    CreateTransactionRevision(
                        transaction_id=paired_transaction_id,
                        facts=paired_facts,
                        created_at=context.recorded_at,
                    ),
                ),
            )
        session.rollback()
        assert session.get(TransactionIdentityRecordModel, transaction_id) is None
        assert session.get(TransactionIdentityRecordModel, paired_transaction_id) is None


def _migration_configs(database_url: str) -> tuple[Config, Config]:
    instrument_root = WORKSPACE_ROOT / "infra" / "instrument_registry"
    instrument_config = Config(str(instrument_root / "alembic.ini"))
    instrument_config.set_main_option(
        "script_location",
        str(instrument_root / "alembic"),
    )
    instrument_config.set_main_option("sqlalchemy.url", database_url)

    portfolio_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    portfolio_config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "alembic"),
    )
    portfolio_config.set_main_option("sqlalchemy.url", database_url)
    return instrument_config, portfolio_config


def _prepare_legacy_migration_database(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Config, Engine]:
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv(
        "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA", "")

    from portfolio_app.core.settings import get_settings

    get_settings.cache_clear()
    instrument_config, portfolio_config = _migration_configs(database_url)
    command.upgrade(instrument_config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    command.upgrade(portfolio_config, "20260713_0035")
    return portfolio_config, engine


def _legacy_transfer_row(
    *,
    transaction_id: str,
    transaction_type: str,
    account_id: str,
    counterparty_account_id: str,
    gross_amount: float = 100.0,
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "transaction_type": transaction_type,
        "account_id": account_id,
        "counterparty_account_id": counterparty_account_id,
        "gross_amount": gross_amount,
    }


@pytest.mark.parametrize(
    ("legacy_rows", "error_pattern"),
    (
        (
            [
                _legacy_transfer_row(
                    transaction_id="legacy-transfer-orphan-out",
                    transaction_type="transfer_out",
                    account_id="legacy-cash-main",
                    counterparty_account_id="legacy-cash-reserve",
                )
            ],
            "requires exactly two legs",
        ),
        (
            [
                _legacy_transfer_row(
                    transaction_id="legacy-transfer-nonreciprocal-out",
                    transaction_type="transfer_out",
                    account_id="legacy-cash-main",
                    counterparty_account_id="legacy-cash-reserve",
                ),
                _legacy_transfer_row(
                    transaction_id="legacy-transfer-nonreciprocal-in",
                    transaction_type="transfer_in",
                    account_id="legacy-cash-reserve",
                    counterparty_account_id="legacy-cash-reserve",
                ),
            ],
            "account links are not reciprocal",
        ),
        (
            [
                _legacy_transfer_row(
                    transaction_id="legacy-transfer-mismatch-out",
                    transaction_type="transfer_out",
                    account_id="legacy-cash-main",
                    counterparty_account_id="legacy-cash-reserve",
                ),
                _legacy_transfer_row(
                    transaction_id="legacy-transfer-mismatch-in",
                    transaction_type="transfer_in",
                    account_id="legacy-cash-reserve",
                    counterparty_account_id="legacy-cash-main",
                    gross_amount=101.0,
                ),
            ],
            "legs differ in gross_amount",
        ),
    ),
)
def test_0036_preflight_rejects_unpaired_legacy_internal_transfers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_rows: list[dict[str, object]],
    error_pattern: str,
) -> None:
    from portfolio_app.core.settings import get_settings

    portfolio_config, engine = _prepare_legacy_migration_database(
        tmp_path / "legacy-transfer-preflight.db",
        monkeypatch,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        securities_count, sort_order
                    ) VALUES (
                        'legacy-transfer-portfolio', 'Legacy Transfer Portfolio',
                        'USD', 'UTC', 'end_of_day', 0, 0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES
                        ('legacy-cash-main', 'legacy-transfer-portfolio',
                         'Legacy Main Cash', 'deposit_account', 'USD', 'active'),
                        ('legacy-cash-reserve', 'legacy-transfer-portfolio',
                         'Legacy Reserve Cash', 'deposit_account', 'USD', 'active')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO transaction_record (
                        transaction_id, portfolio_id, transaction_type,
                        trade_date, trade_time, trade_at, trade_timezone,
                        trade_time_is_estimated, settlement_date,
                        entitlement_date, acquisition_date, account_id,
                        settlement_cash_account_id, instrument_id,
                        instrument_ref_json, quantity, price, gross_amount,
                        counter_amount, fx_rate, fees, taxes, currency,
                        transfer_scope, transfer_object_type, transfer_group_id,
                        counterparty_account_id, note, created_at
                    ) VALUES (
                        :transaction_id, 'legacy-transfer-portfolio',
                        :transaction_type, '2026-04-16', '09:30:00',
                        '2026-04-16T09:30:00+00:00', 'UTC', 0,
                        '2026-04-16', NULL, NULL, :account_id, NULL,
                        NULL, NULL, NULL, NULL, :gross_amount, NULL, NULL,
                        0, 0, 'USD', 'internal_portfolio', 'cash',
                        'legacy-transfer-group', :counterparty_account_id,
                        'Legacy transfer preflight sentinel',
                        '2026-04-16T09:30:00+00:00'
                    )
                    """
                ),
                legacy_rows,
            )

        with pytest.raises(RuntimeError, match=error_pattern):
            command.upgrade(portfolio_config, "head")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260713_0035"
            assert connection.scalar(text("SELECT count(*) FROM transaction_record")) == len(
                legacy_rows
            )
        table_names = set(inspect(engine).get_table_names())
        assert "transaction_record" in table_names
        assert "transaction_revision_record" not in table_names
    finally:
        engine.dispose()
        get_settings.cache_clear()
