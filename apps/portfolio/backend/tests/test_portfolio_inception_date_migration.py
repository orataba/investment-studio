from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260818_0053")

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_migration_backfills_portfolio_inception_from_first_transaction() -> None:
    command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        inception_date = connection.scalar(
            sa.text(
                "SELECT inception_date FROM portfolio_record "
                "WHERE portfolio_id = 'investment-studio'"
            )
        )
        columns = {
            str(column["name"]): column
            for column in sa.inspect(connection).get_columns("portfolio_record")
        }

    assert str(inception_date) == "2026-01-02"
    assert columns["inception_date"]["nullable"] is False


def test_migration_rejects_opening_balance_outside_inferred_inception() -> None:
    with get_engine().begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET transaction_type = 'opening_balance' "
                "WHERE transaction_id = 'txn-0002'"
            )
        )

    with pytest.raises(RuntimeError, match="txn-0002"):
        command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260818_0053"
        )
        assert "inception_date" not in {
            str(column["name"])
            for column in sa.inspect(connection).get_columns("portfolio_record")
        }
