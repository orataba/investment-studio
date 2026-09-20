"""Exercise the actual release entry point without any discovery GET repairing it."""

import importlib.util
from datetime import datetime
from pathlib import Path
import sys

import pytest
from sqlalchemy import delete, event, select, text, update

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.api.routes import watchlists
from watchlist_app.db.models import InstrumentDetail, WatchlistItem
from watchlist_app.db.models.read_models import InstrumentChartReadModel, WatchlistRowReadModel
from watchlist_app.db.models.watchlists import Watchlist, WatchlistUserSettings, WatchlistView, WatchlistViewColumn
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryError
from watchlist_app.services.materialization_policy import WATCHLIST_MATERIALIZATION_VERSION

from .conftest import TEST_SHARED_INSTRUMENTS, seed_shared_instrument


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _release(**kwargs):
    module = _load(Path(__file__).resolve().parents[1] / "scripts/refresh_release_watchlists.py", "release_watchlists")
    with get_session_factory()() as session:
        engine = session.get_bind()
    # Python sqlite3 legacy mode does not BEGIN for the first SELECT. A first
    # nested SAVEPOINT can otherwise commit on release, unlike production PG.
    # Exercise the intended outer transaction rather than that driver artifact.
    def begin(connection):
        connection.exec_driver_sql("BEGIN")
    if engine.dialect.name == "sqlite":
        event.listen(engine, "begin", begin)
    try:
        return module.main(**kwargs)
    finally:
        if engine.dialect.name == "sqlite":
            event.remove(engine, "begin", begin)


def _audit(session):
    # The production gate is unchanged. SQLite's fixture shares namespaces but
    # supports this exact CTE, JSON extraction and null-safe comparison syntax.
    root = Path(__file__).resolve().parents[4]
    query = _load(root / "infra/scripts/audit_live_data.py", "release_audit")._watchlist_system_contract_query()
    return session.scalar(text(query.replace("watchlist.", "").replace("instrument_data.", "")))


def _snapshot(session, models):
    return {model.__tablename__: sorted(
        (dict(row) for row in session.execute(select(model.__table__)).mappings()), key=repr,
    ) for model in models}


def _register(instrument_id, kind):
    seed_shared_instrument({
        "instrument_id": instrument_id, "instrument_name": instrument_id,
        "instrument_type": kind, "currency": "USD",
        "exchange_code": "XNAS" if kind == "equity" else None,
        "identifiers": [{"identifier_type": "ticker", "identifier_value": instrument_id, "is_primary": True}],
        "market_data": [], "lifecycle_state": {"status": "active"},
    })


def test_release_reconciles_registry_before_live_gate_and_preserves_user_state(client):
    assert client.get("/api/watchlists").status_code == 200
    custom = client.post("/api/watchlists", json={"name": "Keep user coverage"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{custom}/items", json={"instrument_ids": ["fund-us-agg"]}).status_code == 200
    for kind in ("equity", "index", "crypto", "other"):
        _register(f"release-{kind}", kind)
    with get_session_factory()() as session:
        # Model a stopped older release: stale membership, absent directory,
        # missing materialized row and old metadata; no API reads after this.
        session.get(Instrument, "fund-us-agg").lifecycle_state_json = {"status": "archived"}
        session.execute(delete(WatchlistRowReadModel).where(WatchlistRowReadModel.watchlist_id == "all-instruments"))
        session.execute(delete(WatchlistItem).where(WatchlistItem.watchlist_id == "all-instruments"))
        session.get(Watchlist, "index").name = "Old label"
        session.get(Watchlist, "index").sort_order = 99
        session.execute(delete(WatchlistRowReadModel).where(WatchlistRowReadModel.watchlist_id == "all-public-funds"))
        session.add(WatchlistUserSettings(user_id="release-owner", ordered_watchlist_ids=[custom, "index", "all-instruments"]))
        session.commit()
        protected = _snapshot(session, [Instrument, WatchlistView, WatchlistViewColumn, WatchlistUserSettings])
        custom_before = _snapshot_custom(session, custom)
        assert _audit(session) > 0

    assert _release() == 0
    with get_session_factory()() as session:
        assert _audit(session) == 0
        assert _snapshot(session, [Instrument, WatchlistView, WatchlistViewColumn, WatchlistUserSettings]) == protected
        assert _snapshot_custom(session, custom) == custom_before
        expected = (set(TEST_SHARED_INSTRUMENTS) - {"fund-us-agg"}) | {"release-equity", "release-index", "release-crypto"}
        assert set(session.scalars(select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == "all-instruments"))) == expected
        # Cross a definite clock boundary without sleeping: unchanged metadata
        # must retain its timestamp even when reconciliation runs much later.
        session.execute(update(InstrumentDetail).values(updated_at=datetime(2001, 1, 1)))
        session.commit()
        before_repeat = _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentDetail])
    assert _release() == 0
    with get_session_factory()() as session:
        assert _audit(session) == 0
        assert _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentDetail]) == before_repeat


def _snapshot_custom(session, watchlist_id):
    return [dict(row) for model in [Watchlist, WatchlistItem, WatchlistRowReadModel]
            for row in session.execute(select(model.__table__).where(model.watchlist_id == watchlist_id)).mappings()]


def test_release_rebuilds_invalidated_chart_and_custom_rows_from_stored_source(client):
    assert client.get("/api/watchlists").status_code == 200
    custom = client.post("/api/watchlists", json={"name": "Keep custom name"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{custom}/items", json={"instrument_ids": ["sxv264"]}).status_code == 200
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, "sxv264")
        expected = chart.payload_json
        chart.payload_json = {"series": []}
        chart.materialization_version = "watchlist-materialization/v11"
        session.get(WatchlistRowReadModel, (custom, "sxv264")).materialization_version = "watchlist-materialization/v11"
        session.commit()
        protected = _snapshot(session, [Instrument, Watchlist, WatchlistItem, WatchlistView, WatchlistViewColumn])
    assert _release() == 0
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, "sxv264")
        assert chart.materialization_version == WATCHLIST_MATERIALIZATION_VERSION
        assert chart.payload_json == expected
        assert session.get(WatchlistRowReadModel, (custom, "sxv264")).materialization_version == WATCHLIST_MATERIALIZATION_VERSION
        assert _snapshot(session, [Instrument, Watchlist, WatchlistItem, WatchlistView, WatchlistViewColumn]) == protected


def test_release_recalc_failure_rolls_back_directory_and_read_models(client, monkeypatch):
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    assert client.get("/api/watchlists").status_code == 200
    assert client.post("/api/recalc/instruments/sxv264/execute", json={"job_type": "all"}).status_code == 200
    with get_session_factory()() as session:
        session.get(Watchlist, "index").name = "Old label"
        session.get(InstrumentChartReadModel, "sxv264").materialization_version = "watchlist-materialization/v11"
        session.commit()
        before = _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentChartReadModel])
    def fail(self, session, **kwargs):
        raise RuntimeError("Injected recalc failure")
    monkeypatch.setattr(CanonicalRecalcService, "execute_recalc", fail)
    with pytest.raises(RuntimeError, match="Injected recalc failure"):
        _release()
    with get_session_factory()() as session:
        assert _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentChartReadModel]) == before


def test_release_requires_explicit_stopped_worker_recovery_and_fences_old_lease(client):
    from watchlist_app.db.models.recalc import RecalcJob
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.canonical_recalc import RecalcJobAlreadyRunningError
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    assert client.get("/api/watchlists").status_code == 200
    assert client.post("/api/recalc/instruments/sxv264/execute", json={"job_type": "all"}).status_code == 200
    repository = SQLAlchemyRecalcJobRepository()
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").materialization_version = "watchlist-materialization/v11"
        job = repository.create(
            session, recalc_job_id="release-interrupted", job_type="all", instrument_id="sxv264",
            trigger_type="manual", trigger_ref_type=None, trigger_ref_id=None, job_status="queued",
            priority=100, dedupe_key=make_recalc_dedupe_key(job_type="all", instrument_id="sxv264"), payload_json={},
        )
        repository.mark_running(session, job)
        old_lease = job.lease_token
        session.commit()
        before = _snapshot(session, [RecalcJob, InstrumentChartReadModel])
    with pytest.raises(RecalcJobAlreadyRunningError):
        _release()
    with get_session_factory()() as session:
        assert _snapshot(session, [RecalcJob, InstrumentChartReadModel]) == before
    assert _release(recover_interrupted=True) == 0
    with get_session_factory()() as session:
        job = session.get(RecalcJob, "release-interrupted")
        assert job.job_status == "completed"
        assert job.lease_token != old_lease
        assert not repository.mark_completed(session, job, lease_token=old_lease)
        assert not repository.touch_heartbeat(session, job.recalc_job_id, old_lease)
        assert session.get(InstrumentChartReadModel, "sxv264").materialization_version == WATCHLIST_MATERIALIZATION_VERSION


@pytest.mark.parametrize("failure", ["registry", "materialization"])
def test_release_registry_failure_rolls_back_all_system_lists(client, monkeypatch, failure):
    assert client.get("/api/watchlists").status_code == 200
    _register("release-index", "index")
    with get_session_factory()() as session:
        session.get(Watchlist, "all-instruments").name = "Old label"
        session.commit()
        before = _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentDetail])
    if failure == "registry":
        original = watchlists.list_shared_instrument_identities
        def fail(**kwargs):
            if kwargs.get("instrument_type") == "index":
                raise SharedInstrumentRegistryError("Injected registry failure")
            return original(**kwargs)
        monkeypatch.setattr(watchlists, "list_shared_instrument_identities", fail)
    else:
        original = watchlists._materialize_watchlist_rows
        def fail(session, **kwargs):
            original(session, **kwargs)
            if kwargs["watchlist_id"] == "index":
                raise SharedInstrumentRegistryError("Injected materialization failure")
        monkeypatch.setattr(watchlists, "_materialize_watchlist_rows", fail)
    with pytest.raises(SharedInstrumentRegistryError, match="Injected"):
        _release()
    with get_session_factory()() as session:
        assert _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentDetail]) == before


def test_release_derives_migrated_screener_without_changing_chart_generation(client):
    assert client.get("/api/watchlists").status_code == 200
    assert client.post("/api/recalc/instruments/sxv264/execute", json={"job_type": "all"}).status_code == 200
    preserved_columns = [column for column in InstrumentChartReadModel.__table__.c if column.name != "screener_payload_json"]
    with get_session_factory()() as session:
        session.get(InstrumentChartReadModel, "sxv264").screener_payload_json = None
        session.commit()
        before = session.execute(select(*preserved_columns).where(InstrumentChartReadModel.instrument_id == "sxv264")).one()
    assert _release() == 0
    with get_session_factory()() as session:
        after = session.execute(select(*preserved_columns).where(InstrumentChartReadModel.instrument_id == "sxv264")).one()
        assert after == before
        projection = session.get(InstrumentChartReadModel, "sxv264").screener_payload_json
        assert "series" not in projection
        assert set(projection["sparklines"]) == {"return_chart_1d", "return_chart_1w", "return_chart_1m", "return_chart_1y"}


def test_screener_backfill_includes_archived_charts_and_rolls_back_failed_batch(client, monkeypatch):
    assert client.get("/api/watchlists").status_code == 200
    module = _load(Path(__file__).resolve().parents[1] / "scripts/refresh_release_watchlists.py", "release_screener_backfill")
    for instrument_id in ("sxv264", "savf63"):
        assert client.post(f"/api/recalc/instruments/{instrument_id}/execute", json={"job_type": "all"}).status_code == 200
    with get_session_factory()() as session:
        session.get(InstrumentDetail, "sxv264").is_active = False
        for instrument_id in ("sxv264", "savf63"):
            session.get(InstrumentChartReadModel, instrument_id).screener_payload_json = None
        session.commit()
        before = _snapshot(session, [InstrumentChartReadModel])
    build = module.build_screener_chart_projection
    calls = []

    def fail_after_first(instrument_id, payload):
        calls.append(instrument_id)
        if len(calls) == 2:
            raise RuntimeError("projection build failed")
        return build(instrument_id, payload)

    monkeypatch.setattr(module, "build_screener_chart_projection", fail_after_first)
    with pytest.raises(RuntimeError, match="projection build failed"):
        with get_session_factory()() as session, session.begin():
            module._backfill_screener_projections(session)
    with get_session_factory()() as session:
        assert _snapshot(session, [InstrumentChartReadModel]) == before
    monkeypatch.setattr(module, "build_screener_chart_projection", build)
    with get_session_factory()() as session, session.begin():
        assert module._backfill_screener_projections(session) == 2
    with get_session_factory()() as session:
        assert session.get(InstrumentChartReadModel, "sxv264").screener_payload_json is not None
        assert session.get(InstrumentDetail, "sxv264").is_active is False
