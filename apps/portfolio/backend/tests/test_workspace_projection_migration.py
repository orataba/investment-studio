from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.models import PortfolioRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


def _config():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_engine().url.render_as_string(hide_password=False))
    return config


def _assert_rebuild_preserves_facts():
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(
            portfolio_id="migration-no-state", portfolio_name="No previous calculation",
            base_currency="USD", valuation_timezone="UTC",
            valuation_cutoff_policy="close", inception_date=date(2026, 1, 1),
        ))
        session.commit()
    with get_engine().begin() as connection:
        before = connection.execute(sa.text("SELECT * FROM transaction_record ORDER BY portfolio_id, transaction_id")).all()
        ids = set(connection.execute(sa.text("SELECT portfolio_id FROM portfolio_record")).scalars())
        existing_id = next(pid for pid in ids if pid != "migration-no-state")
        connection.execute(sa.text("DELETE FROM portfolio_calculation_state WHERE portfolio_id = :id"), {"id": existing_id})
        connection.execute(sa.text(
            "INSERT INTO portfolio_calculation_state "
            "(portfolio_id, daily_snapshot_status, dirty_from, refresh_request_id, error_message) "
            "VALUES (:id, 'failed', '2026-09-19', 'old-generation', 'old failure')"
        ), {"id": existing_id})
    command.upgrade(_config(), "20260920_0065")
    with get_engine().connect() as connection:
        assert connection.execute(sa.text("SELECT * FROM transaction_record ORDER BY portfolio_id, transaction_id")).all() == before
        rows = connection.execute(sa.text(
            "SELECT portfolio_id, daily_snapshot_status, dirty_from, refresh_request_id, error_message "
            "FROM portfolio_calculation_state"
        )).all()
        assert {row[0] for row in rows} == ids
        assert len({row[3] for row in rows}) == len(ids)
        assert all(row[1] == "stale" and row[2] is None and row[3] != "old-generation" and row[4] is None for row in rows)
        assert "portfolio_workspace_read_model" in sa.inspect(connection).get_table_names()
    command.downgrade(_config(), "20260920_0064")
    assert "portfolio_workspace_read_model" not in sa.inspect(get_engine()).get_table_names()


@pytest.mark.migration_base_revision("20260920_0064")
def test_projection_migration_invalidates_all_existing_results_without_changing_facts():
    _assert_rebuild_preserves_facts()


@pytest.mark.postgresql_integration
def test_postgres_projection_migration_rebuilds_existing_and_missing_states(postgres_portfolio_env):
    command.downgrade(_config(), "20260920_0064")
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(
            portfolio_id="migration-existing", portfolio_name="Previous calculation",
            base_currency="USD", valuation_timezone="UTC",
            valuation_cutoff_policy="close", inception_date=date(2026, 1, 1),
        ))
        session.commit()
    _assert_rebuild_preserves_facts()
