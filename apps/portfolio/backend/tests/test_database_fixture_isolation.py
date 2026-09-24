from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


@pytest.mark.parametrize("iteration", [1, 2])
def test_each_test_has_its_own_schema_and_seed(
    migrated_portfolio_schema: Path, iteration: int
) -> None:
    engine = get_engine()
    assert Path(engine.url.database) != migrated_portfolio_schema
    with engine.begin() as connection:
        assert "fixture_mutation" not in sa.inspect(connection).get_table_names()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM portfolio_record"
        ).scalar_one() > 0
        connection.exec_driver_sql("CREATE TABLE fixture_mutation (value INTEGER)")
        connection.exec_driver_sql(
            "INSERT INTO fixture_mutation (value) VALUES (?)", (iteration,)
        )
        connection.exec_driver_sql("DELETE FROM portfolio_record")

    with sqlite3.connect(f"{migrated_portfolio_schema.as_uri()}?mode=ro", uri=True) as template:
        assert template.execute("SELECT COUNT(*) FROM portfolio_record").fetchone()[0] == 0
        assert template.execute(
            "SELECT name FROM sqlite_master WHERE name = 'fixture_mutation'"
        ).fetchall() == []
