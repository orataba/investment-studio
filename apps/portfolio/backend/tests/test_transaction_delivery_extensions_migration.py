from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260902_0058")


def config():
    root = Path(__file__).resolve().parents[1]
    result = Config(str(root / "alembic.ini"))
    result.set_main_option("script_location", str(root / "alembic"))
    result.set_main_option("sqlalchemy.url", str(get_engine().url))
    return result


def test_delivery_extension_migration_preserves_legacy_facts_and_reverses_when_unused():
    with get_engine().connect() as connection:
        before = connection.execute(sa.text("SELECT transaction_id, gross_amount FROM transaction_record ORDER BY transaction_id")).all()
    command.upgrade(config(), "20260905_0059")
    with get_engine().connect() as connection:
        assert before == connection.execute(sa.text("SELECT transaction_id, gross_amount FROM transaction_record ORDER BY transaction_id")).all()
        assert {"asset_deliveries_json", "lot_selections_json"} <= {item["name"] for item in sa.inspect(connection).get_columns("transaction_record")}
    command.downgrade(config(), "20260902_0058")


def test_delivery_extension_downgrade_refuses_to_erase_recorded_cash_purpose():
    command.upgrade(config(), "20260905_0059")
    with get_engine().begin() as connection:
        connection.execute(sa.text("UPDATE account_record SET cash_purpose='margin' WHERE account_category='cash'"))
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(config(), "20260902_0058")
    with get_engine().connect() as connection:
        assert connection.execute(sa.text("SELECT cash_purpose FROM account_record WHERE account_category='cash' LIMIT 1")).scalar_one() == "margin"
