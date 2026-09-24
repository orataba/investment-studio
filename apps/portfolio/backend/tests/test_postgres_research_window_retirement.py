from alembic import command
import pytest
import sqlalchemy as sa

from .test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config
from .test_research_window_retirement import _verify_migration_round_trip


pytestmark = pytest.mark.postgresql_integration


def test_postgres_removes_only_retired_preferences_and_preserves_archives(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0068")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _verify_migration_round_trip(engine, config, schema="portfolio")
    finally:
        engine.dispose()
