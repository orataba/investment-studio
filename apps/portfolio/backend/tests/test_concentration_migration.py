from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine

pytestmark = pytest.mark.migration_base_revision("20260908_0062")


def config():
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url))
    return cfg


def test_upgrade_preserves_ledger_and_unused_policy_can_be_removed():
    with get_engine().connect() as connection:
        before = connection.execute(sa.text("SELECT portfolio_id, transaction_id, quantity FROM transaction_record ORDER BY portfolio_id, transaction_id")).all()
    command.upgrade(config(), "20260908_0063")
    with get_engine().connect() as connection:
        assert "concentration_policy_revision" in sa.inspect(connection).get_table_names()
        assert connection.execute(sa.text("SELECT portfolio_id, transaction_id, quantity FROM transaction_record ORDER BY portfolio_id, transaction_id")).all() == before
    command.downgrade(config(), "20260908_0062")
    assert "concentration_policy_revision" not in sa.inspect(get_engine()).get_table_names()


def test_policy_downgrade_does_not_erase_saved_limits():
    command.upgrade(config(), "20260908_0063")
    with get_engine().begin() as connection:
        pid = connection.scalar(sa.text("SELECT portfolio_id FROM portfolio_record LIMIT 1"))
        connection.execute(sa.text("INSERT INTO concentration_policy_revision (portfolio_id, revision, effective_from, settings_json, created_by, created_at) VALUES (:pid, 1, '2026-09-08', '{}', 'pm', '2026-09-08T00:00:00Z')"), {"pid": pid})
    with pytest.raises(RuntimeError, match="Cannot erase"):
        command.downgrade(config(), "20260908_0062")
