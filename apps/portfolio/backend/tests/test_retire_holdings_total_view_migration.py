from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260824_0056")

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_migration_removes_only_the_retired_holdings_total_view() -> None:
    with get_engine().begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO portfolio_table_view_store ("
                "portfolio_id, view_scope, store_json, created_at, updated_at"
                ") VALUES "
                "('investment-studio', 'holdings_total', '{}', '2026-09-02', '2026-09-02'), "
                "('investment-studio', 'holdings_fcn', '{}', '2026-09-02', '2026-09-02')"
            )
        )

    command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        scopes = connection.scalars(
            sa.text(
                "SELECT view_scope FROM portfolio_table_view_store "
                "WHERE portfolio_id = 'investment-studio' ORDER BY view_scope"
            )
        ).all()

    assert scopes == ["holdings_fcn"]
