"""Exercise the deployed constraint name and terminal status on PostgreSQL."""
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa

from portfolio_app.db.models import PortfolioRecordModel, TransactionCaptureBatchModel
from portfolio_app.db.session import get_engine, get_session_factory
from tests.test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config  # noqa: F401

pytestmark = pytest.mark.postgresql_integration


def test_postgres_capture_pause_constraint_preserves_attempt(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20261008_0072")
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(
            portfolio_id="capture-migration", portfolio_name="Capture migration", base_currency="USD",
            valuation_timezone="UTC", valuation_cutoff_policy="eod", inception_date=date(2026, 1, 1)))
        session.flush()
        session.add(TransactionCaptureBatchModel(
            batch_id="retained-attempt", portfolio_id="capture-migration", purpose="transaction_import",
            content_key="a" * 64, capture_count=1, analysis_run_status="failed", analysis_run_attempt=2,
            analysis_run_error="Existing error retained verbatim", created_at="2026-10-01T00:00:00Z",
            updated_at="2026-10-02T00:00:00Z"))
        session.commit()
    query = sa.text("SELECT * FROM transaction_capture_batch WHERE batch_id='retained-attempt'")
    change_status = sa.text("UPDATE transaction_capture_batch SET analysis_run_status=:status WHERE batch_id='retained-attempt'")
    with get_engine().connect() as connection:
        before = dict(connection.execute(query).mappings().one())
    with pytest.raises(sa.exc.IntegrityError), get_engine().begin() as connection:
        connection.execute(change_status, {"status": "paused"})
    command.upgrade(config, "20261012_0073")
    with get_engine().connect() as connection:
        assert dict(connection.execute(query).mappings().one()) == before
    with get_engine().begin() as connection:
        connection.execute(change_status, {"status": "paused"})
    with pytest.raises(RuntimeError, match="cannot preserve"):
        command.downgrade(config, "20261008_0072")
    with get_engine().connect() as connection:
        assert dict(connection.execute(query).mappings().one()) == {**before, "analysis_run_status": "paused"}
    # With no paused facts a rollback remains possible and the next upgrade
    # must find the historical (PostgreSQL-truncated) generated constraint again.
    with get_engine().begin() as connection:
        connection.execute(change_status, {"status": "failed"})
    command.downgrade(config, "20261008_0072")
    command.upgrade(config, "20261012_0073")
    with get_engine().connect() as connection:
        assert dict(connection.execute(query).mappings().one()) == before
