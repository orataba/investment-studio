from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import select

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskReviewRule
from watchlist_app.db.models.watchlists import Watchlist, WatchlistUserSettings, WatchlistView, WatchlistViewColumn
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository, SYSTEM_WATCHLIST_SPECS
from watchlist_app.services.shared_instrument_registry import get_shared_instrument

from .conftest import TEST_SHARED_INSTRUMENTS, canonical_quote_policy, seed_shared_instrument


def _rows(client, watchlist_id, *, view_id="overview"):
    response = client.post("/api/screener/query", json={
        "watchlist_id": watchlist_id,
        "view_id": view_id,
        "selected_fields": ["instrument_name", "instrument_type", "attr.coverage_status"],
        "sort": [], "group_by": "none", "pagination": {"page": 1, "page_size": 100},
    })
    assert response.status_code == 200, response.text
    return {row["instrument_id"]: row for row in response.json()["rows"]}


def test_directory_covers_registered_supported_assets_and_reconciles_archival(client: TestClient):
    for kind in ("equity", "index", "other"):
        seed_shared_instrument({
            "instrument_id": f"registered-{kind}", "instrument_name": f"Registered {kind}",
            "instrument_type": kind, "currency": "USD",
            "exchange_code": "XNAS" if kind == "equity" else None,
            "quote_selection_policy": canonical_quote_policy(kind) if kind != "other" else None,
            "identifiers": [{"identifier_type": "ticker", "identifier_value": f"REG-{kind}", "is_primary": True}],
            "market_data": [], "lifecycle_state": {"status": "active"},
        })
    response = client.get("/api/watchlists/all-instruments")
    assert response.status_code == 200
    assert response.json()["owner_type"] == "system"
    assert set(response.json()["instrument_types"]) == {"public_fund", "private_fund", "etf", "equity", "index"}
    expected = set(TEST_SHARED_INSTRUMENTS) | {"registered-equity", "registered-index"}
    assert set(_rows(client, "all-instruments")) == expected
    # The mixed classification view must retain every supported type as well.
    assert set(_rows(client, "all-instruments", view_id="classification")) == expected

    with get_session_factory()() as session:
        instrument = session.get(Instrument, "registered-equity")
        instrument.lifecycle_state_json = {"status": "archived"}
        session.commit()
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    assert set(_rows(client, "all-instruments")) == expected - {"registered-equity"}
    # Registry archival affects directory membership, never deletes the canonical asset.
    with get_session_factory()() as session:
        assert session.get(Instrument, "registered-equity") is not None


def test_lists_share_status_and_removal_preserves_instrument_research_and_settings(client: TestClient):
    iid = "fund-us-agg"
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    list_ids = []
    for name in ("First Coverage", "Second Coverage"):
        created = client.post("/api/watchlists", json={"name": name})
        assert created.status_code == 200
        wid = created.json()["watchlist_id"]
        list_ids.append(wid)
        assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": [iid]}).status_code == 200

    updated = client.post(f"/api/instrument-attributes/instruments/{iid}", json={
        "values": [{"attribute_key": "coverage_status", "value": "Invested"}],
    })
    assert updated.status_code == 200
    with get_session_factory()() as session:
        for wid in [*list_ids, "all-instruments"]:
            row = SQLAlchemyReadModelRepository().list_watchlist_rows(session, wid)
            assert next(item for item in row if item.instrument_id == iid).attributes_json["coverage_status"] == "Invested"
        topic = ResearchTopic(topic_id=f"dossier:{iid}", title="Retained research", instrument_ids=[iid], visibility="team")
        session.add(topic)
        session.flush()
        session.add(ResearchEntry(entry_id="directory-research", topic_id=topic.topic_id,
            kind="note", title="Original research", body="Keep this analysis", context_json={"instrument_ids": [iid]}))
        session.add(RiskReviewRule(instrument_id=iid, drawdown_limit=12.0))
        session.commit()
        attribute_ids = list(session.scalars(select(InstrumentAttributeValue.instrument_attribute_value_id).where(
            InstrumentAttributeValue.instrument_id == iid)))
    original_identity = deepcopy(get_shared_instrument(iid))

    assert client.post(f"/api/watchlists/{list_ids[0]}/items/delete", json={"instrument_ids": [iid]}).status_code == 200
    assert client.delete(f"/api/watchlists/{list_ids[1]}").status_code == 200
    assert iid in _rows(client, "all-instruments")
    assert client.get(f"/api/instrument-attributes/instruments/{iid}").json()["values"]["coverage_status"] == "Invested"
    assert get_shared_instrument(iid) == original_identity
    with get_session_factory()() as session:
        assert session.get(InstrumentDetail, iid) is not None
        assert session.get(ResearchTopic, f"dossier:{iid}").instrument_ids == [iid]
        assert session.get(ResearchEntry, "directory-research").body == "Keep this analysis"
        assert session.get(RiskReviewRule, iid).drawdown_limit == 12.0
        assert list(session.scalars(select(InstrumentAttributeValue.instrument_attribute_value_id).where(
            InstrumentAttributeValue.instrument_id == iid))) == attribute_ids
        assert set(session.scalars(select(WatchlistItem.watchlist_id).where(
            WatchlistItem.instrument_id == iid))) == {"all-instruments"}


def test_directory_membership_is_read_only_but_can_be_copied(client: TestClient):
    # Reserving the ID before first directory discovery avoids a user list claiming it.
    custom = client.post("/api/watchlists", json={"name": "All Instruments"})
    assert custom.status_code == 200
    target = custom.json()["watchlist_id"]
    assert target != "all-instruments"
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    iid = "fund-us-agg"
    assert client.delete("/api/watchlists/all-instruments").status_code == 400
    assert client.post("/api/watchlists/all-instruments/items", json={"instrument_ids": [iid]}).status_code == 400
    assert client.post("/api/watchlists/all-instruments/items/delete", json={"instrument_ids": [iid]}).status_code == 400
    assert client.post("/api/watchlists/all-instruments/items/move", json={
        "instrument_ids": [iid], "target_watchlist_id": target,
    }).status_code == 400
    copied = client.post("/api/watchlists/all-instruments/items/copy", json={
        "instrument_ids": [iid], "target_watchlist_id": target,
    })
    assert copied.status_code == 200
    assert copied.json()["added_count"] == 1
    assert iid in _rows(client, "all-instruments")
    assert iid in _rows(client, target)


def test_system_refresh_updates_reference_metadata_without_replacing_views_or_personal_order(client: TestClient):
    assert client.get("/api/watchlists").status_code == 200
    repository = SQLAlchemyWatchlistRepository()
    with get_session_factory()() as session:
        for spec in SYSTEM_WATCHLIST_SPECS:
            record = session.get(Watchlist, spec.watchlist_id)
            record.name = "Old system label"
            record.description = "Old system description"
            record.sort_order = 99
        overview = session.get(WatchlistView, "index::overview")
        overview.default_group_by = "currency"
        overview.default_filters_json = {"currency": ["USD"]}
        personal = repository.create_view(session, watchlist_id="index", view_id="personal",
            name="Personal view", description="Preserve this layout", kind="custom", default_group_by="none",
            default_sort=[{"field_key": "instrument_name", "direction": "desc"}],
            default_filters={}, default_advanced_filter={},
            columns=[{"field_key": "instrument_name", "display_order": 0, "width": 440}], is_default=True)
        personal.author_user_id = "review-owner"
        order = ["all-private-funds", "index", "all-public-funds", "all-instruments"]
        session.add(WatchlistUserSettings(user_id="review-owner", ordered_watchlist_ids=order))
        session.commit()
        saved_views = list(session.execute(select(WatchlistView.__table__)).mappings())
        saved_columns = list(session.execute(select(WatchlistViewColumn.__table__)).mappings())

    assert client.get("/api/watchlists").status_code == 200
    assert client.get("/api/watchlists/index").status_code == 200
    with get_session_factory()() as session:
        for position, spec in enumerate(SYSTEM_WATCHLIST_SPECS):
            record = session.get(Watchlist, spec.watchlist_id)
            assert (record.name, record.description, record.sort_order) == (spec.name, spec.description, position)
        assert list(session.execute(select(WatchlistView.__table__)).mappings()) == saved_views
        assert list(session.execute(select(WatchlistViewColumn.__table__)).mappings()) == saved_columns
        assert session.get(WatchlistUserSettings, "review-owner").ordered_watchlist_ids == order
