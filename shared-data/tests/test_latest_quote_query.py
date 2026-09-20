"""Latest-date selection retains every series and legacy join boundary."""
from copy import deepcopy
from datetime import date
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from investment_studio_instrument_core import instrument_store
from investment_studio_instrument_core.db_models import InstrumentMarketData


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgresql_integration)])
def quote_engine(request):
    admin = None
    if request.param == "sqlite":
        engine = create_engine("sqlite+pysqlite:///:memory:")
    else:
        target = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
        if not target:
            pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
        url = make_url(target)
        name = f"investment_studio_latest_quotes_{uuid4().hex[:8]}"
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        engine = create_engine(url.set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}"')
            admin.dispose()


def loose_quote_table(engine):
    # Deliberately omit modern NOT NULL/unique/check/FK constraints to exercise
    # pre-contract rows. Production constraints are tested separately.
    metadata = MetaData()
    table = Table("instrument_market_data", metadata, *[
        Column(column.name, column.type)
        for column in InstrumentMarketData.__table__.columns
    ])
    metadata.create_all(engine)
    return table


def point(provider, **overrides):
    return dict(instrument_market_data_id=None, instrument_id="a", metric_family="price",
        quote_basis="close", as_of_date=date(2026, 9, 18), value="100", currency="USD",
        price_unit="per_unit", price_scale=1, provider=provider, status="complete",
        nav_lineage_kind=None, nav_derivation_method_version=None,
        nav_derivation_anchor_date=None, nav_lineage_evidence_json=None,
        fund_nav_adjustment_factor_id=None) | overrides


def test_latest_query_preserves_series_dates_same_date_ties_and_null_join_semantics(quote_engine):
    table = loose_quote_table(quote_engine)
    rows = [
        point("old", as_of_date=date(2026, 9, 17)),
        point("same-date-first"), point("same-date-second", value="101"),
        point("different-basis", quote_basis="adjusted_close", value="99"),
        point("different-currency", currency="EUR"),
        point("legacy-currency", currency="usd"),
        point("legacy-unit", price_unit="rate"),
        point("legacy-scale", price_scale=100),
        point("old-available", quote_basis="last", as_of_date=date(2026, 9, 17)),
        point("latest-unavailable", quote_basis="last", status="unavailable", value=None),
        point("different-family", metric_family="fx", quote_basis="spot", price_unit="rate"),
        point("null-date", as_of_date=None),
        point("null-currency", currency=None),
        point("null-unit", price_unit=None),
        point("null-scale", price_scale=None),
        point("all-null-dates", instrument_id="c", as_of_date=None),
        point("other-instrument", instrument_id="b"),
    ]
    with quote_engine.begin() as connection:
        connection.execute(table.insert(), rows)
    with Session(quote_engine) as session:
        result = instrument_store._latest_market_data_for_instruments(session, ["a", "c", "missing"])
        assert instrument_store._latest_market_data_for_instruments(session, []) == {}
    expected = {"same-date-first", "same-date-second", "different-basis", "different-currency",
                "legacy-currency", "legacy-unit", "legacy-scale", "latest-unavailable", "different-family"}
    assert {row["provider"] for row in result["a"]} == expected
    assert len(result["a"]) == len(expected)
    assert result["c"] == result["missing"] == []
    latest = next(row for row in result["a"] if row["provider"] == "latest-unavailable")
    assert latest["status"] == "unavailable" and latest["value"] is None
    assert {row["as_of_date"] for row in result["a"]} == {"2026-09-18"}


def test_latest_query_retains_nav_evidence_and_fresh_read_isolation(quote_engine):
    table = loose_quote_table(quote_engine)
    evidence = {"source_field": "official_nav", "observation": {"ids": ["old", "latest"]}}
    nav = point("nav", metric_family="nav", quote_basis="official_nav",
                nav_lineage_kind="provider_explicit", nav_lineage_evidence_json=evidence)
    with quote_engine.begin() as connection:
        connection.execute(table.insert(), [nav])
    with Session(quote_engine) as session:
        first = instrument_store._latest_market_data_for_instruments(session, ["a"])
    expected = deepcopy(first)
    first["a"][0]["nav_lineage"]["evidence"]["observation"]["ids"].clear()
    with Session(quote_engine) as session:
        assert instrument_store._latest_market_data_for_instruments(session, ["a"]) == expected
    with quote_engine.begin() as connection:
        connection.execute(table.update().values(nav_lineage_evidence_json=None))
    with Session(quote_engine) as session, pytest.raises(ValueError):
        instrument_store._latest_market_data_for_instruments(session, ["a"])


def test_latest_quote_index_migration_preserves_rows_and_unique_identity(quote_engine):
    table = loose_quote_table(quote_engine)
    unique_name = "uq_instrument_market_data_instrument_metric_basis_date_currency"
    index_name = "ix_instrument_market_data_series_latest"
    with quote_engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE UNIQUE INDEX {unique_name} ON instrument_market_data "
                                   "(instrument_id, metric_family, quote_basis, as_of_date, currency)")
        connection.execute(table.insert(), [point("latest"), point("old", as_of_date=date(2026, 9, 17))])
        before = connection.execute(select(table).order_by(table.c.as_of_date)).all()
        path = Path(__file__).parents[1] / "instruments/alembic/versions/20260920_0036_latest_quote_series_index.py"
        spec = importlib.util.spec_from_file_location("latest_quote_index_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            indexes = {index["name"]: index for index in inspect(connection).get_indexes(table.name)}
            assert index_name in indexes and indexes[unique_name]["unique"]
            assert indexes[index_name]["column_names"] == ["instrument_id", "metric_family", "quote_basis",
                                                          "currency", "price_unit", "price_scale", "as_of_date"]
            if connection.dialect.name == "postgresql":
                definition = connection.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname=:name"), {"name": index_name})
                assert "as_of_date DESC" in definition
            else:
                key_rows = connection.exec_driver_sql(f"PRAGMA index_xinfo('{index_name}')").all()
                assert next(row for row in key_rows if row[2] == "as_of_date")[3] == 1
            assert connection.execute(select(table).order_by(table.c.as_of_date)).all() == before
            migration.downgrade()
        indexes = {index["name"] for index in inspect(connection).get_indexes(table.name)}
        assert index_name not in indexes and unique_name in indexes
        assert connection.execute(select(table).order_by(table.c.as_of_date)).all() == before
