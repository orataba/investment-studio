import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260908_0061")


def config():
    root = Path(__file__).resolve().parents[1]
    result = Config(str(root / "alembic.ini"))
    result.set_main_option("script_location", str(root / "alembic"))
    result.set_main_option("sqlalchemy.url", str(get_engine().url))
    return result


def transaction_rows(connection):
    return [dict(row) for row in connection.execute(sa.text(
        "SELECT * FROM transaction_record ORDER BY portfolio_id, transaction_id"
    )).mappings()]


def test_cashflow_migration_preserves_all_legacy_values_and_reverses_when_unused():
    with get_engine().connect() as connection:
        before = transaction_rows(connection)
    assert before
    command.upgrade(config(), "20260908_0062")
    with get_engine().connect() as connection:
        upgraded = transaction_rows(connection)
        assert all(row.pop("settlement_cashflows_json") is None for row in upgraded)
        assert upgraded == before
    command.downgrade(config(), "20260908_0061")
    with get_engine().connect() as connection:
        assert transaction_rows(connection) == before
        assert "settlement_cashflows_json" not in {column["name"] for column in sa.inspect(connection).get_columns("transaction_record")}


def test_cashflow_downgrade_refuses_to_erase_recorded_settlement_cashflows():
    command.upgrade(config(), "20260908_0062")
    cashflows = json.dumps([{"kind": "coupon", "cash_account_id": "test-usd-cash", "currency": "USD", "amount": "3333.33", "recognition_date": "2026-06-03", "settlement_date": "2026-06-05"}])
    with get_engine().begin() as connection:
        first = transaction_rows(connection)[0]
        connection.execute(sa.text(
            "UPDATE transaction_record SET settlement_cashflows_json = :cashflows "
            "WHERE portfolio_id = :portfolio_id AND transaction_id = :transaction_id"
        ), {**first, "cashflows": cashflows})
        before = transaction_rows(connection)
    with pytest.raises(RuntimeError, match="Cannot remove recorded FCN settlement cashflows"):
        command.downgrade(config(), "20260908_0061")
    with get_engine().connect() as connection:
        assert transaction_rows(connection) == before
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20260908_0062"
