from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.watchlists import Watchlist, WatchlistItem
from yungu_instrument_core import instrument_store as shared_store


pytestmark = pytest.mark.postgresql_integration

DEFAULT_POSTGRES_URL = "postgresql+psycopg://yungu:yungu@127.0.0.1:5432/yungu"


def _run_instrument_registry_upgrade() -> None:
    config = Config(str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
    )
    command.upgrade(config, "head")


def _run_watchlist_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _run_watchlist_upgrade_until_fk(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "1b7d2e8c4f90")


def _seed_required_instrument_registry_rows(database_url: str, instrument_ids: list[str]) -> None:
    empty_json = json.dumps({})
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for instrument_id in instrument_ids:
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM instrument_registry.instrument WHERE instrument_id = :instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                ).scalar()
                if exists:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO instrument_registry.instrument (
                            instrument_id,
                            instrument_name,
                            instrument_type,
                            currency,
                            quote_selection_policy_json,
                            source_settings_json,
                            refresh_status_json,
                            lifecycle_state_json
                        )
                        VALUES (
                            :instrument_id,
                            :instrument_name,
                            :instrument_type,
                            :currency,
                            CAST(:quote_selection_policy_json AS jsonb),
                            CAST(:source_settings_json AS jsonb),
                            CAST(:refresh_status_json AS jsonb),
                            CAST(:lifecycle_state_json AS jsonb)
                        )
                        """
                    ),
                    {
                        "instrument_id": instrument_id,
                        "instrument_name": instrument_id.replace("-", " ").title(),
                        "instrument_type": "fund",
                        "currency": "USD",
                        "quote_selection_policy_json": empty_json,
                        "source_settings_json": empty_json,
                        "refresh_status_json": empty_json,
                        "lifecycle_state_json": json.dumps({"status": "active"}),
                    },
                )
    finally:
        engine.dispose()


def _seed_instrument_ids_required_by_watchlist_baseline(database_url: str) -> None:
    _run_watchlist_upgrade_until_fk(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT instrument_id
                    FROM (
                        SELECT instrument_id FROM watchlist.instrument_detail
                        UNION
                        SELECT instrument_id FROM watchlist.watchlist_item
                    ) AS seeded_instruments
                    WHERE instrument_id IS NOT NULL
                    ORDER BY instrument_id
                    """
                )
            )
            instrument_ids = [str(row.instrument_id) for row in rows]
    finally:
        engine.dispose()
    _seed_required_instrument_registry_rows(database_url, instrument_ids)
    _run_watchlist_upgrade(database_url)


def _admin_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(database="postgres").render_as_string(hide_password=False)


@pytest.fixture
def postgres_watchlist_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    base_database_url = os.getenv("YUNGU_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"yungu_watchlist_fk_{uuid4().hex[:8]}"
    database_url = make_url(base_database_url).set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment-dependent skip
        pytest.skip(f"PostgreSQL is not available for integration test: {exc}")
    finally:
        admin_engine.dispose()

    monkeypatch.setenv("YUNGU_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("YUNGU_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry")
    monkeypatch.setenv("FTV2_DATABASE_URL", database_url)
    monkeypatch.setenv("FTV2_ALEMBIC_DATABASE_URL", database_url)
    monkeypatch.setenv("FTV2_DATABASE_SCHEMA", "watchlist")

    from watchlist_app.core import settings as settings_module
    from watchlist_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_instrument_registry_upgrade()
    _seed_instrument_ids_required_by_watchlist_baseline(database_url)

    session_factory = session_module.get_session_factory()
    identifier_value = f"WATCHFK{uuid4().hex[:8].upper()}"
    instrument = shared_store.create_instrument(
        session_factory,
        instrument_name="Watchlist FK Integration Asset",
        instrument_type="fund",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": identifier_value,
                "is_primary": True,
            }
        ],
    )

    yield {
        "database_url": database_url,
        "database_schema": "watchlist",
        "instrument_id": str(instrument["instrument_id"]),
    }

    cleanup_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    try:
        with cleanup_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        cleanup_engine.dispose()

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


def test_watchlist_instrument_registry_foreign_keys_are_enforced(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module

    engine = create_engine(postgres_watchlist_env["database_url"])
    try:
        with engine.connect() as connection:
            constraints = connection.execute(
                text(
                    """
                    SELECT
                        con.conname AS constraint_name,
                        cls.relname AS table_name,
                        ref_ns.nspname AS referred_schema,
                        ref_cls.relname AS referred_table
                    FROM pg_constraint con
                    JOIN pg_class cls
                        ON cls.oid = con.conrelid
                    JOIN pg_namespace cls_ns
                        ON cls_ns.oid = cls.relnamespace
                    JOIN pg_class ref_cls
                        ON ref_cls.oid = con.confrelid
                    JOIN pg_namespace ref_ns
                        ON ref_ns.oid = ref_cls.relnamespace
                    WHERE cls_ns.nspname = :schema
                      AND cls.relname IN ('instrument_detail', 'watchlist_item')
                      AND con.conname IN (
                        'fk_instrument_detail_instrument_id_instrument',
                        'fk_watchlist_item_instrument_id_instrument'
                      )
                    """
                ),
                {"schema": postgres_watchlist_env["database_schema"]},
            ).mappings().all()
    finally:
        engine.dispose()

    by_name = {item["constraint_name"]: item for item in constraints}
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["table_name"] == "watchlist_item"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_schema"] == "instrument_registry"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_table"] == "instrument"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["table_name"] == "instrument_detail"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_schema"] == "instrument_registry"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_table"] == "instrument"

    session_factory = session_module.get_session_factory()
    watchlist_id = f"watchlist-fk-{uuid4().hex[:8]}"

    with session_factory() as session:
        session.add(
            Watchlist(
                watchlist_id=watchlist_id,
                name="Constraint Verification Watchlist",
                description=None,
                owner_type="user",
                owner_id="integration",
                is_default=False,
                is_shared=False,
                sort_order=0,
            )
        )
        session.add(
            InstrumentDetail(
                instrument_id=postgres_watchlist_env["instrument_id"],
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Watchlist FK Integration Asset",
                primary_identifier_type="ticker",
                primary_identifier_value="WATCHFK",
                is_active=True,
                metadata_json={},
            )
        )
        session.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=postgres_watchlist_env["instrument_id"],
                added_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        session.commit()

    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id=f"missing-{uuid4().hex[:8]}",
                instrument_type="fund",
                detail_view_type="fund",
                instrument_name="Missing Shared Instrument",
                primary_identifier_type="ticker",
                primary_identifier_value="MISSINGFK",
                is_active=True,
                metadata_json={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_factory() as session:
        session.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=f"missing-{uuid4().hex[:8]}",
                added_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
