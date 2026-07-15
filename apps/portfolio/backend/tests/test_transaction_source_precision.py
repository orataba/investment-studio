from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.models import TransactionRecordModel
from portfolio_app.db.session import get_engine, get_session_factory


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_transaction_source_amount_and_quantity_survive_without_display_rounding(client) -> None:
    deposit = client.post(
        "/api/portfolios/portfolio-ops/transactions",
        json={
            "transaction_type": "deposit",
            "trade_date": "2026-04-16",
            "account_id": "cash-usd-main",
            "gross_amount": "123.12345678",
            "currency": "USD",
        },
    )
    assert deposit.status_code == 200
    deposit_payload = deposit.json()
    assert deposit_payload["source_gross_amount"] == "123.12345678"
    assert deposit_payload["gross_amount"] == pytest.approx(123.12345678)

    transfer = client.post(
        "/api/portfolios/portfolio-ops/transactions/internal-transfer",
        json={
            "trade_date": "2026-04-16",
            "transfer_object_type": "position",
            "from_account_id": "broker-us-core",
            "to_account_id": "broker-us-income",
            "instrument_id": "fund-us-agg",
            "quantity": "0.123456789012",
            "note": "Precision-preserving position transfer",
        },
    )
    assert transfer.status_code == 200, transfer.text
    assert {
        item["source_quantity"]
        for item in transfer.json()["transactions"]
    } == {"0.123456789012"}

    session_factory = get_session_factory()
    with session_factory() as session:
        stored_deposit = session.get(
            TransactionRecordModel,
            deposit_payload["transaction_id"],
        )
        assert stored_deposit is not None
        assert stored_deposit.source_gross_amount == Decimal("123.12345678")


@pytest.mark.migration_base_revision("20260715_0038")
def test_source_precision_migration_backfills_without_replacing_float_projections() -> None:
    engine = get_engine()
    config = _alembic_config()
    with engine.connect() as connection:
        transaction_count = connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record"))

    command.downgrade(config, "20260715_0036")
    try:
        with engine.connect() as connection:
            columns_before = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record")) == transaction_count
        assert "source_quantity" not in columns_before
        assert "source_gross_amount" not in columns_before

        command.upgrade(config, "20260715_0037")
        with engine.connect() as connection:
            columns_after = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
            projection, source = connection.execute(
                sa.text(
                    "SELECT gross_amount, source_gross_amount "
                    "FROM transaction_record ORDER BY transaction_id LIMIT 1"
                )
            ).one()
        assert "source_quantity" in columns_after
        assert "source_gross_amount" in columns_after
        assert float(source) == pytest.approx(float(projection))
    finally:
        command.upgrade(config, "head")


@pytest.mark.migration_base_revision("20260715_0038")
def test_source_precision_preflight_fails_before_ddl_for_out_of_range_legacy_value() -> None:
    engine = get_engine()
    config = _alembic_config()
    command.downgrade(config, "20260715_0036")
    transaction_id: str | None = None
    original_gross_amount: float | None = None
    try:
        with engine.begin() as connection:
            transaction_id, original_gross_amount = connection.execute(
                sa.text(
                    "SELECT transaction_id, gross_amount "
                    "FROM transaction_record ORDER BY transaction_id LIMIT 1"
                )
            ).one()
            connection.execute(
                sa.text(
                    "UPDATE transaction_record SET gross_amount = :value "
                    "WHERE transaction_id = :transaction_id"
                ),
                {"value": 1e21, "transaction_id": transaction_id},
            )

        with pytest.raises(RuntimeError, match="preflight failed before schema changes"):
            command.upgrade(config, "20260715_0037")

        with engine.connect() as connection:
            columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
        assert "source_gross_amount" not in columns
    finally:
        if transaction_id is not None and original_gross_amount is not None:
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE transaction_record SET gross_amount = :value "
                        "WHERE transaction_id = :transaction_id"
                    ),
                    {
                        "value": original_gross_amount,
                        "transaction_id": transaction_id,
                    },
                )
        command.upgrade(config, "head")
