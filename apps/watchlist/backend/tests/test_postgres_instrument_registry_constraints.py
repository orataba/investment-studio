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
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "shared-data" / "instruments" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.watchlists import Watchlist, WatchlistItem
from investment_studio_instrument_core import instrument_store as shared_store


pytestmark = pytest.mark.postgresql_integration


def test_dossier_writer_waits_for_publication_and_preserves_new_user_focus(postgres_watchlist_env):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from sqlalchemy import select
    from studio_identity import current_principal, principal_context
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_dossier import read_mandate, save_mandate, ResearchMandateInput
    iid = postgres_watchlist_env["instrument_id"]
    actor = current_principal()
    factory = get_session_factory()
    def payload_from(value, **updates):
        return ResearchMandateInput.model_validate({
            **{key: value[key] for key in ResearchMandateInput.model_fields}, **updates})
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Concurrent Research", metadata_json={}))
        session.commit()
        initial = payload_from(read_mandate(session, iid), focus=["原用户重点"])
        save_mandate(session, iid, initial)
    entered = Event()
    def research_writer():
        with principal_context(actor), factory() as session:
            before = read_mandate(session, iid)  # An object cached before the other writer commits.
            payload = payload_from(before, background="本轮研究补充")
            entered.set()
            return save_mandate(session, iid, payload, origin="research")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as session:
            session.execute(select(InstrumentDetail.instrument_id).where(InstrumentDetail.instrument_id == iid).with_for_update())
            future = pool.submit(research_writer)
            assert entered.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
                updated = payload_from(read_mandate(session, iid), focus=["新用户重点"])
                save_mandate(session, iid, updated)
            finally:
                session.rollback()
        result = future.result(timeout=5)
    assert result["user_focus"] == ["新用户重点"]
    assert result["version_id"].endswith(":v3")
    assert [row["user_focus"] for row in result["versions"]] == [["原用户重点"], ["新用户重点"]]


def test_pm_note_write_lock_refreshes_cached_revision_before_edit(postgres_watchlist_env):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from datetime import date
    from threading import Event
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
    iid = postgres_watchlist_env["instrument_id"]
    repo, factory = SQLAlchemyInstrumentResearchRepository(), get_session_factory()
    values = {"note_date": date(2026, 9, 8), "note_type": "thesis_update", "title": "原观点", "importance": "medium"}
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Concurrent PM", metadata_json={}))
        session.flush()
        repo.create_note(session, instrument_id=iid, note_id="shared-note", values=values, updated_by="pm-one")
        session.commit()
    entered = Event()
    def edit_second():
        with factory() as session:
            cached = repo.get_note(session, iid, "shared-note")
            assert cached.revision_number == 1
            entered.set()
            record = repo.get_note(session, iid, "shared-note", for_update=True)
            repo.update_note(session, record=record, values={**values, "title": "第三版本"}, updated_by="pm-one")
            session.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as session:
            record = repo.get_note(session, iid, "shared-note", for_update=True)
            future = pool.submit(edit_second)
            assert entered.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
                repo.update_note(session, record=record, values={**values, "title": "第二版本"}, updated_by="pm-one")
                session.commit()
            finally:
                session.rollback()
        future.result(timeout=5)
    with factory() as session:
        assert repo.get_note(session, iid, "shared-note").revision_number == 3
        assert {r.revision_number: r.title for r in repo.list_note_revisions(session, iid)} == {
            1: "原观点", 2: "第二版本", 3: "第三版本"}


def test_first_risk_requests_share_one_durable_run(postgres_watchlist_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import select
    from studio_identity import current_principal, principal_context
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.risk_officer import begin_run
    actor, ready, factory = current_principal(), Barrier(2), get_session_factory()
    def start():
        with principal_context(actor), factory() as session:
            ready.wait(timeout=5)
            run, created = begin_run(session, watchlist_id="first-risk-scope")
            return run.entry_id, created
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    with factory() as session:
        assert len(list(session.scalars(select(ResearchEntry).where(ResearchEntry.entry_id == results[0][0])))) == 1
        assert session.get(ResearchTopic, "risk-officer:watchlist:first-risk-scope") is not None

def _instrument_registry_config() -> Config:
    config = Config(str(WORKSPACE_ROOT / "shared-data" / "instruments" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "shared-data" / "instruments" / "alembic"),
    )
    return config


def _run_watchlist_upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


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
                        "instrument_type": "public_fund",
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
    base_database_url = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not base_database_url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    database_name = f"investment_studio_watchlist_fk_{uuid4().hex[:8]}"
    database_url = make_url(base_database_url).set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - depends on configured service
        pytest.fail(f"Configured PostgreSQL integration target is unavailable: {exc}")
    finally:
        admin_engine.dispose()

    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "instrument_data")
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_DATABASE_SCHEMA", "watchlist")

    from watchlist_app.core import settings as settings_module
    from watchlist_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    engine = session_module.get_engine()
    try:
        command.upgrade(_instrument_registry_config(), "20260902_0029")
        _seed_instrument_ids_required_by_watchlist_baseline(database_url)
        command.upgrade(_instrument_registry_config(), "head")

        instrument = shared_store.create_instrument(
            session_module.get_session_factory(),
            instrument_name="Watchlist FK Integration Asset",
            instrument_type="public_fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": f"WATCHFK{uuid4().hex[:8].upper()}",
                    "is_primary": True,
                }
            ],
        )

        yield {
            "database_url": database_url,
            "database_schema": "watchlist",
            "instrument_id": str(instrument["instrument_id"]),
        }
    finally:
        # Close our pool before dropping the fixture database. Killing every
        # pg_stat_activity row also targets superuser-owned autovacuum workers.
        engine.dispose()
        session_module.get_session_factory.cache_clear()
        session_module.get_engine.cache_clear()
        settings_module.get_settings.cache_clear()
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            admin_engine.dispose()


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
            membership_constraints = set(
                connection.scalars(
                    text(
                        """
                        SELECT con.conname
                        FROM pg_constraint con
                        JOIN pg_class cls ON cls.oid = con.conrelid
                        JOIN pg_namespace cls_ns ON cls_ns.oid = cls.relnamespace
                        WHERE cls_ns.nspname = :schema
                          AND cls.relname = 'watchlist_item'
                        """
                    ),
                    {"schema": postgres_watchlist_env["database_schema"]},
                )
            )
            field_identity = connection.execute(
                text(
                    """
                    SELECT
                        count(*) FILTER (
                            WHERE field_key = 'instrument_name'
                              AND source_metric_code =
                                  'watchlist_row_read_model.instrument_name'
                        ) AS canonical_count,
                        count(*) FILTER (
                            WHERE field_key = 'asset_name'
                               OR source_metric_code =
                                  'watchlist_row_read_model.asset_name'
                        ) AS legacy_count
                    FROM watchlist.field_registry
                    """
                )
            ).mappings().one()
    finally:
        engine.dispose()

    by_name = {item["constraint_name"]: item for item in constraints}
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["table_name"] == "watchlist_item"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_schema"] == "instrument_data"
    assert by_name["fk_watchlist_item_instrument_id_instrument"]["referred_table"] == "instrument"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["table_name"] == "instrument_detail"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_schema"] == "instrument_data"
    assert by_name["fk_instrument_detail_instrument_id_instrument"]["referred_table"] == "instrument"
    assert "uq_watchlist_item_watchlist_instrument" in membership_constraints
    assert "uq_watchlist_item_watchlist_asset" not in membership_constraints
    assert field_identity["canonical_count"] == 1
    assert field_identity["legacy_count"] == 0

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
                instrument_type="public_fund",
                detail_view_type="public_fund",
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
                instrument_type="public_fund",
                detail_view_type="public_fund",
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


def test_postgres_primary_display_field_reconciles_upgraded_database(
    postgres_watchlist_env: dict[str, str],
) -> None:
    # Replay the historical Watchlist migration against its contemporary schema.
    command.downgrade(_instrument_registry_config(), "20260902_0029")
    engine = create_engine(postgres_watchlist_env["database_url"])
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA watchlist CASCADE"))
            connection.execute(text("CREATE SCHEMA watchlist"))
        _run_watchlist_upgrade(
            postgres_watchlist_env["database_url"],
            "20260809_0035",
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO watchlist.watchlist (
                        watchlist_id, name, description, owner_type, owner_id,
                        is_default, is_shared, sort_order
                    ) VALUES (
                        'migration-watchlist', 'Migration Watchlist', NULL,
                        'user', 'migration-test', true, false, 0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO watchlist.watchlist_view (
                        watchlist_view_id, watchlist_id, name, description,
                        kind, default_sort_json, default_filters_json,
                        default_advanced_filter_json, default_group_by,
                        density, is_default, created_at
                    ) VALUES (
                        'migration-view', 'migration-watchlist',
                        'Migration View', NULL, 'table',
                        CAST('[{"field":"instrument_name","direction":"asc"}]' AS json),
                        CAST('{"instrument_name":"Audit"}' AS json),
                        CAST('{}' AS json), 'instrument_name', NULL, true, now()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO watchlist.watchlist_view_column (
                        watchlist_view_id, field_key, display_order, width,
                        is_visible, pin_side
                    ) VALUES (
                        'migration-view', 'instrument_name', 0, 320, true, NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE watchlist.field_registry
                    SET field_key = 'asset_name',
                        source_metric_code = 'watchlist_row_read_model.asset_name'
                    WHERE field_key = 'instrument_name'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE watchlist.watchlist_view_column
                    SET field_key = 'asset_name'
                    WHERE field_key = 'instrument_name'
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE watchlist.watchlist_view
                    SET default_sort_json =
                            CAST('[{"field":"asset_name","direction":"asc"}]' AS json),
                        default_filters_json = CAST('{"asset_name":"Audit"}' AS json),
                        default_group_by = 'asset_name'
                    WHERE watchlist_view_id = 'migration-view'
                    """
                )
            )
        _run_watchlist_upgrade(
            postgres_watchlist_env["database_url"],
            "20260809_0036",
        )

        with engine.connect() as connection:
            canonical_count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM watchlist.field_registry
                    WHERE field_key = 'instrument_name'
                      AND source_metric_code =
                          'watchlist_row_read_model.instrument_name'
                    """
                )
            )
            legacy_count = connection.scalar(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM watchlist.field_registry
                         WHERE field_key = 'asset_name')
                        +
                        (SELECT count(*) FROM watchlist.watchlist_view_column
                         WHERE field_key = 'asset_name')
                    """
                )
            )
            view = connection.execute(
                text(
                    """
                    SELECT default_sort_json, default_filters_json,
                           default_group_by
                    FROM watchlist.watchlist_view
                    WHERE watchlist_view_id = 'migration-view'
                    """
                )
            ).mappings().one()
        assert canonical_count == 1
        assert legacy_count == 0
        assert view["default_sort_json"] == [
            {"field": "instrument_name", "direction": "asc"}
        ]
        assert view["default_filters_json"] == {"instrument_name": "Audit"}
        assert view["default_group_by"] == "instrument_name"
    finally:
        engine.dispose()


def test_recalc_instrument_advisory_lock_serializes_transactions(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository

    repository = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    with session_factory() as first, session_factory() as second:
        assert repository.acquire_instrument_lock(
            first,
            instrument_id=instrument_id,
            wait=False,
        ) is True
        assert repository.acquire_instrument_lock(
            second,
            instrument_id=instrument_id,
            wait=False,
        ) is False
        first.commit()
        assert repository.acquire_instrument_lock(
            second,
            instrument_id=instrument_id,
            wait=False,
        ) is True
        second.rollback()


def test_postgres_recalc_claim_serializes_job_types_for_one_instrument(
    postgres_watchlist_env: dict[str, str],
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

    repository = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    with session_factory() as session:
        session.add(
            InstrumentDetail(
                instrument_id=instrument_id,
                instrument_type="public_fund",
                detail_view_type="public_fund",
                instrument_name="PostgreSQL claim asset",
                primary_identifier_type="ticker",
                primary_identifier_value="CLAIMLOCK",
                is_active=True,
                metadata_json={},
            )
        )
        for job_type in ("performance", "exposure"):
            repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type=job_type,
                instrument_id=instrument_id,
                trigger_type="integration_test",
                trigger_ref_type=None,
                trigger_ref_id=None,
                job_status="queued",
                priority=90,
                dedupe_key=make_recalc_dedupe_key(
                    job_type=job_type,
                    instrument_id=instrument_id,
                ),
                payload_json={"requested_by": "integration_test"},
            )
        session.commit()

    with session_factory() as session:
        first = repository.claim_next_queued(session)
        assert first is not None
        first_job_id = first.recalc_job_id
        first_lease = str(first.lease_token)
        session.commit()

    with session_factory() as session:
        assert repository.claim_next_queued(session) is None
        running = repository.get(session, first_job_id)
        assert running is not None
        assert repository.mark_completed(
            session,
            running,
            lease_token=first_lease,
            payload_json={"integration_test": True},
        ) is True
        session.commit()

    with session_factory() as session:
        second = repository.claim_next_queued(session)
        assert second is not None
        assert second.recalc_job_id != first_job_id
        assert second.instrument_id == instrument_id
        session.rollback()
