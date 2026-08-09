from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260807_0044")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "20260809_0045"
MIGRATION_PARENT = "20260807_0044"
REMOVED_COLUMNS = {"event_group_id", "related_instrument_id"}
REMOVED_INDEXES = {
    "ix_transaction_record_portfolio_event_group",
    "ix_transaction_record_portfolio_related_instrument",
}


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def _seed_legacy_delivery_group(
    *,
    duplicate_lifecycle: bool = False,
    currency_mismatch: bool = False,
) -> tuple[object, object]:
    engine = get_engine()
    with engine.begin() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT transaction_id, portfolio_id, account_id, currency "
                "FROM transaction_record ORDER BY transaction_id LIMIT 3"
            )
        ).mappings().all()
        required = 3 if duplicate_lifecycle else 2
        assert len(rows) >= required
        lifecycle_rows = rows[:2] if duplicate_lifecycle else rows[:1]
        delivery_row = rows[2] if duplicate_lifecycle else rows[1]
        reference = lifecycle_rows[0]
        for lifecycle_row in lifecycle_rows:
            connection.execute(
                sa.text(
                    "UPDATE transaction_record SET "
                    "portfolio_id = :portfolio_id, account_id = :account_id, "
                    "currency = :currency, transaction_type = 'maturity_redemption', "
                    "lifecycle_event_type = 'fcn_physical_settlement', "
                    "event_group_id = 'invalid-delivery-group', "
                    "related_instrument_id = 'migration-asset-1', "
                    "gross_amount = 0, source_gross_amount = 0 "
                    "WHERE transaction_id = :transaction_id"
                ),
                {
                    "portfolio_id": reference["portfolio_id"],
                    "account_id": reference["account_id"],
                    "currency": reference["currency"],
                    "transaction_id": lifecycle_row["transaction_id"],
                },
            )
        delivery_currency = reference["currency"]
        if currency_mismatch:
            delivery_currency = "HKD" if str(delivery_currency).upper() != "HKD" else "USD"
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET "
                "portfolio_id = :portfolio_id, account_id = :account_id, "
                "currency = :currency, transaction_type = 'buy', "
                "lifecycle_event_type = NULL, "
                "event_group_id = 'invalid-delivery-group', "
                "related_instrument_id = NULL, "
                "gross_amount = 125000, source_gross_amount = 125000 "
                "WHERE transaction_id = :transaction_id"
            ),
            {
                "portfolio_id": reference["portfolio_id"],
                "account_id": reference["account_id"],
                "currency": delivery_currency,
                "transaction_id": delivery_row["transaction_id"],
            },
        )
    return reference["transaction_id"], reference["portfolio_id"]


def test_derivative_event_group_removal_converts_fcn_pair_and_drops_schema() -> None:
    engine = get_engine()
    config = _alembic_config()

    with engine.begin() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT transaction_id, portfolio_id, account_id, currency, trade_date "
                "FROM transaction_record ORDER BY transaction_id LIMIT 2"
            )
        ).mappings().all()
        assert len(rows) == 2
        fcn_row, asset_row = rows
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET "
                "transaction_type = 'maturity_redemption', "
                "lifecycle_event_type = 'fcn_physical_settlement', "
                "event_group_id = 'migration-delivery-1', "
                "related_instrument_id = 'migration-asset-1', "
                "gross_amount = 0, source_gross_amount = 0 "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": fcn_row["transaction_id"]},
        )
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET "
                "portfolio_id = :portfolio_id, account_id = :account_id, currency = :currency, "
                "transaction_type = 'buy', lifecycle_event_type = NULL, "
                "event_group_id = 'migration-delivery-1', "
                "related_instrument_id = NULL, "
                "gross_amount = 125000, source_gross_amount = 125000 "
                "WHERE transaction_id = :transaction_id"
            ),
            {
                "portfolio_id": fcn_row["portfolio_id"],
                "account_id": fcn_row["account_id"],
                "currency": fcn_row["currency"],
                "transaction_id": asset_row["transaction_id"],
            },
        )
        state_exists = connection.execute(
            sa.text(
                "SELECT 1 FROM portfolio_calculation_state "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": fcn_row["portfolio_id"]},
        ).first()
        if state_exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO portfolio_calculation_state "
                    "(portfolio_id, daily_snapshot_status, dirty_from) "
                    "VALUES (:portfolio_id, 'current', NULL)"
                ),
                {"portfolio_id": fcn_row["portfolio_id"]},
            )
        else:
            connection.execute(
                sa.text(
                    "UPDATE portfolio_calculation_state SET "
                    "daily_snapshot_status = 'current', dirty_from = NULL "
                    "WHERE portfolio_id = :portfolio_id"
                ),
                {"portfolio_id": fcn_row["portfolio_id"]},
            )

    try:
        command.upgrade(config, MIGRATION_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            columns = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            indexes = {
                str(index["name"])
                for index in inspector.get_indexes("transaction_record")
            }
            lifecycle_checks = [
                str(check.get("sqltext") or "")
                for check in inspector.get_check_constraints("transaction_record")
                if str(check.get("name") or "").endswith(
                    "transaction_record_lifecycle_event_type"
                )
            ]
            converted = connection.execute(
                sa.text(
                    "SELECT lifecycle_event_type, gross_amount, source_gross_amount "
                    "FROM transaction_record WHERE transaction_id = :transaction_id"
                ),
                {"transaction_id": fcn_row["transaction_id"]},
            ).mappings().one()
            calculation_state = connection.execute(
                sa.text(
                    "SELECT daily_snapshot_status, dirty_from "
                    "FROM portfolio_calculation_state "
                    "WHERE portfolio_id = :portfolio_id"
                ),
                {"portfolio_id": fcn_row["portfolio_id"]},
            ).mappings().one()

        assert REMOVED_COLUMNS.isdisjoint(columns)
        assert REMOVED_INDEXES.isdisjoint(indexes)
        assert converted["lifecycle_event_type"] == "fcn_knock_in"
        assert float(converted["gross_amount"]) == pytest.approx(125_000.0)
        assert float(converted["source_gross_amount"]) == pytest.approx(125_000.0)
        assert calculation_state["daily_snapshot_status"] == "stale"
        assert str(calculation_state["dirty_from"]) == str(fcn_row["trade_date"])
        assert lifecycle_checks
        assert "fcn_physical_settlement" not in lifecycle_checks[0]

        with pytest.raises(RuntimeError, match="pre-migration backup"):
            command.downgrade(config, MIGRATION_PARENT)
    finally:
        command.upgrade(config, "head")


def test_derivative_event_group_removal_rejects_duplicate_lifecycle_facts() -> None:
    engine = get_engine()
    config = _alembic_config()
    lifecycle_id, _ = _seed_legacy_delivery_group(duplicate_lifecycle=True)

    with pytest.raises(RuntimeError, match="exactly one physical-settlement fact"):
        command.upgrade(config, MIGRATION_REVISION)

    with engine.connect() as connection:
        unchanged = connection.execute(
            sa.text(
                "SELECT lifecycle_event_type, gross_amount, event_group_id "
                "FROM transaction_record WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": lifecycle_id},
        ).mappings().one()
        revision = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    assert unchanged["lifecycle_event_type"] == "fcn_physical_settlement"
    assert float(unchanged["gross_amount"]) == pytest.approx(0.0)
    assert unchanged["event_group_id"] == "invalid-delivery-group"
    assert revision == MIGRATION_PARENT


def test_derivative_event_group_removal_rejects_cross_currency_amount_copy() -> None:
    engine = get_engine()
    config = _alembic_config()
    lifecycle_id, _ = _seed_legacy_delivery_group(currency_mismatch=True)

    with pytest.raises(RuntimeError, match="across currencies or accounts"):
        command.upgrade(config, MIGRATION_REVISION)

    with engine.connect() as connection:
        unchanged = connection.execute(
            sa.text(
                "SELECT lifecycle_event_type, gross_amount, event_group_id "
                "FROM transaction_record WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": lifecycle_id},
        ).mappings().one()
        revision = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    assert unchanged["lifecycle_event_type"] == "fcn_physical_settlement"
    assert float(unchanged["gross_amount"]) == pytest.approx(0.0)
    assert unchanged["event_group_id"] == "invalid-delivery-group"
    assert revision == MIGRATION_PARENT
