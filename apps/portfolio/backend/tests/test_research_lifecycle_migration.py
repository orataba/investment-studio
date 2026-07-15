from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.migration_base_revision("20260715_0038")


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    from portfolio_app.core.settings import get_settings

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def test_research_pm_approval_migration_is_additive_backfilled_and_reversible() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    engine = get_engine()
    command.downgrade(config, "20260715_0037")

    try:
        with engine.connect() as connection:
            before_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns(
                    "portfolio_instrument_universe_record"
                )
            }
        assert "research_pm_approved" not in before_columns
        assert "research_pm_approved_at" not in before_columns

        command.upgrade(config, "20260715_0038")
        with engine.connect() as connection:
            after_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns(
                    "portfolio_instrument_universe_record"
                )
            }
            approval_values = connection.execute(
                sa.text(
                    "SELECT research_pm_approved "
                    "FROM portfolio_instrument_universe_record"
                )
            ).scalars().all()
        assert {"research_pm_approved", "research_pm_approved_at"}.issubset(after_columns)
        assert all(value in (False, 0) for value in approval_values)

        command.downgrade(config, "20260715_0037")
        with engine.connect() as connection:
            downgraded_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns(
                    "portfolio_instrument_universe_record"
                )
            }
        assert "research_pm_approved" not in downgraded_columns
        assert "research_pm_approved_at" not in downgraded_columns
    finally:
        command.upgrade(config, "head")
