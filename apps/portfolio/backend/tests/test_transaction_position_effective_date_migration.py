from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260716_0040")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "20260728_0041"
MIGRATION_PARENT = "20260716_0040"
INDEX_NAME = "ix_transaction_record_portfolio_position_effective"
CHECK_NAME = "ck_transaction_record_position_effective_date"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_position_effective_date_migration_is_additive_and_reversible() -> None:
    engine = get_engine()
    config = _alembic_config()

    with engine.connect() as connection:
        transaction_count = connection.scalar(
            sa.text("SELECT COUNT(*) FROM transaction_record")
        )
        columns_before = {
            str(column["name"])
            for column in sa.inspect(connection).get_columns("transaction_record")
        }
    assert transaction_count
    assert "position_effective_date" not in columns_before

    try:
        command.upgrade(config, MIGRATION_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            columns_after = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            indexes_after = {
                str(index["name"])
                for index in inspector.get_indexes("transaction_record")
            }
            checks_after = {
                str(check["name"])
                for check in inspector.get_check_constraints("transaction_record")
            }
            effective_dates = connection.execute(
                sa.text(
                    "SELECT position_effective_date "
                    "FROM transaction_record"
                )
            ).scalars().all()

        assert "position_effective_date" in columns_after
        assert INDEX_NAME in indexes_after
        assert CHECK_NAME in checks_after
        assert len(effective_dates) == transaction_count
        assert all(value is None for value in effective_dates)

        command.downgrade(config, MIGRATION_PARENT)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            columns_downgraded = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            indexes_downgraded = {
                str(index["name"])
                for index in inspector.get_indexes("transaction_record")
            }
        assert "position_effective_date" not in columns_downgraded
        assert INDEX_NAME not in indexes_downgraded
    finally:
        command.upgrade(config, "head")
