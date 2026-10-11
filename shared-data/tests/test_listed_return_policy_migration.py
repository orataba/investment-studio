"""A persisted return policy must not reintroduce raw-price analytical fallbacks."""
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from investment_studio_instrument_core.models import validate_quote_selection_policy_for_instrument_type


POLICY = {"trading": ["last", "close"], "valuation": ["close", "last"],
          "total_return": ["adjusted_close", "close", "last"], "chart": ["close"], "reference": ["last"]}
MIGRATION = Path(__file__).parents[1] / "instruments/alembic/versions/20261008_0037_listed_total_return_policy.py"


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgresql_integration)])
def migration_engine(request, tmp_path):
    admin = None
    if request.param == "sqlite":
        engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'return-policy.db'}")
    else:
        target = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
        if not target:
            pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
        url = make_url(target)
        name = f"investment_studio_return_policy_{uuid4().hex[:8]}"
        admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        engine = sa.create_engine(url.set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        if admin:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            admin.dispose()


def test_migration_preserves_roles_advances_generation_and_enforces_direct_sql(migration_engine):
    table = sa.Table("instrument", sa.MetaData(),
        sa.Column("instrument_id", sa.String, primary_key=True),
        sa.Column("instrument_type", sa.String, nullable=False),
        sa.Column("quote_selection_policy_json", sa.JSON, nullable=False),
        sa.Column("calculation_inputs_updated_at", sa.String))
    table.create(migration_engine)
    source = spec_from_file_location("listed_return_policy_migration", MIGRATION)
    migration = module_from_spec(source)
    source.loader.exec_module(migration)
    unchanged = {**POLICY, "total_return": ["adjusted_close"]}
    future = "2099-01-01T00:00:00.000000Z"
    with migration_engine.begin() as connection:
        for instrument_id, kind, policy in [("eq", "equity", POLICY), ("etf", "etf", POLICY),
                                            ("strict", "etf", unchanged), ("idx", "index", POLICY)]:
            connection.execute(table.insert().values(instrument_id=instrument_id, instrument_type=kind,
                quote_selection_policy_json=deepcopy(policy), calculation_inputs_updated_at=future))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = {row["instrument_id"]: row for row in connection.execute(sa.select(table)).mappings()}
        for instrument_id in ("eq", "etf"):
            assert rows[instrument_id]["quote_selection_policy_json"] == unchanged
            assert rows[instrument_id]["calculation_inputs_updated_at"] > future
        assert rows["idx"]["quote_selection_policy_json"] == POLICY
        assert rows["strict"]["calculation_inputs_updated_at"] == future
        for invalid in (["close"], ["adjusted_close", "last"], [], None, {"0": "adjusted_close"}):
            with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
                connection.execute(table.update().where(table.c.instrument_id == "eq").values(
                    quote_selection_policy_json={**POLICY, "total_return": invalid}))
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(table.insert().values(instrument_id="invalid", instrument_type="etf",
                                                     quote_selection_policy_json={}))
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(table.update().where(table.c.instrument_id == "idx").values(instrument_type="equity"))


@pytest.mark.parametrize("kind", ["equity", "etf"])
def test_runtime_rejects_old_policy_and_keeps_display_roles(kind):
    with pytest.raises(ValueError, match="must use only adjusted_close"):
        validate_quote_selection_policy_for_instrument_type(instrument_type=kind, quote_selection_policy=POLICY)
    policy = validate_quote_selection_policy_for_instrument_type(
        instrument_type=kind, quote_selection_policy={**POLICY, "total_return": ["adjusted_close"]})
    assert policy.chart == ["close"]
    assert policy.valuation == ["close", "last"]
