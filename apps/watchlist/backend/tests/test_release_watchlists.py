"""Exercise the actual release entry point without any discovery GET repairing it."""

import importlib.util
from datetime import datetime
from pathlib import Path
import sys

import pytest
from sqlalchemy import delete, select, text, update

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.api.routes import watchlists
from watchlist_app.db.models import InstrumentDetail, WatchlistItem
from watchlist_app.db.models.read_models import WatchlistRowReadModel
from watchlist_app.db.models.watchlists import Watchlist, WatchlistUserSettings, WatchlistView, WatchlistViewColumn
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryError

from .conftest import TEST_SHARED_INSTRUMENTS, seed_shared_instrument


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _release():
    return _load(Path(__file__).resolve().parents[1] / "scripts/refresh_release_watchlists.py", "release_watchlists").main()


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


@pytest.mark.parametrize("failure", ["registry", "materialization"])
def test_release_registry_failure_rolls_back_all_system_lists(client, monkeypatch, failure):
    assert client.get("/api/watchlists").status_code == 200
    _register("release-index", "index")
    with get_session_factory()() as session:
        session.get(Watchlist, "all-instruments").name = "Old label"
        session.commit()
        before = _snapshot(session, [Watchlist, WatchlistItem, WatchlistRowReadModel, InstrumentDetail])
    if failure == "registry":
        original = watchlists.list_shared_instruments
        def fail(**kwargs):
            if kwargs.get("instrument_type") == "index":
                raise SharedInstrumentRegistryError("Injected registry failure")
            return original(**kwargs)
        monkeypatch.setattr(watchlists, "list_shared_instruments", fail)
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
