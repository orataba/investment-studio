from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260902_0057")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "20260902_0057"
PHYSICAL_DELIVERY_REVISION = "20260902_0058"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def _lifecycle_check_sql(connection: sa.Connection) -> str:
    checks = sa.inspect(connection).get_check_constraints("transaction_record")
    lifecycle_check = next(
        constraint
        for constraint in checks
        if str(constraint.get("name") or "").endswith("lifecycle_event_type")
    )
    return str(lifecycle_check["sqltext"])


def test_option_physical_delivery_schema_upgrades_and_reverses_when_empty() -> None:
    config = _alembic_config()
    command.upgrade(config, PHYSICAL_DELIVERY_REVISION)

    with get_engine().connect() as connection:
        inspector = sa.inspect(connection)
        assert "option_delivery_link" in inspector.get_table_names()
        assert inspector.get_pk_constraint("option_delivery_link")["constrained_columns"] == [
            "option_transaction_id"
        ]
        assert any(
            constraint["column_names"] == ["stock_transaction_id"]
            for constraint in inspector.get_unique_constraints("option_delivery_link")
        )
        assert any(
            index["name"] == "ix_option_delivery_link_portfolio_underlying"
            and index["column_names"] == ["portfolio_id", "underlying_instrument_id"]
            for index in inspector.get_indexes("option_delivery_link")
        )
        lifecycle_sql = _lifecycle_check_sql(connection)
        assert "option_long_exercise" in lifecycle_sql
        assert "option_writer_assignment" in lifecycle_sql

    command.downgrade(config, PREVIOUS_REVISION)

    with get_engine().connect() as connection:
        assert "option_delivery_link" not in sa.inspect(connection).get_table_names()
        lifecycle_sql = _lifecycle_check_sql(connection)
        assert "option_long_exercise" not in lifecycle_sql
        assert "option_writer_assignment" not in lifecycle_sql
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            PREVIOUS_REVISION
        )

    command.upgrade(config, "head")


def test_option_physical_delivery_downgrade_refuses_to_drop_physical_facts() -> None:
    config = _alembic_config()
    command.upgrade(config, PHYSICAL_DELIVERY_REVISION)
    engine = get_engine()
    with engine.begin() as connection:
        transaction_id = connection.scalar(
            sa.text(
                "SELECT transaction_id FROM transaction_record "
                "WHERE lifecycle_event_type IS NULL ORDER BY transaction_id LIMIT 1"
            )
        )
        assert transaction_id is not None
        connection.execute(
            sa.text(
                "UPDATE transaction_record "
                "SET lifecycle_event_type = 'option_long_exercise' "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": transaction_id},
        )

    try:
        with pytest.raises(RuntimeError, match="physical-delivery facts exist"):
            command.downgrade(config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                PHYSICAL_DELIVERY_REVISION
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE transaction_record SET lifecycle_event_type = NULL "
                    "WHERE transaction_id = :transaction_id"
                ),
                {"transaction_id": transaction_id},
            )
        command.upgrade(config, "head")
