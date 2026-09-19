from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import event, select

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem
from watchlist_app.db.models.read_models import WatchlistRowReadModel
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskReviewRule
from watchlist_app.db.models.watchlists import InstrumentTaxonomyAssignmentHistory, Watchlist, WatchlistUserSettings, WatchlistView, WatchlistViewColumn
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository, SYSTEM_WATCHLIST_SPECS
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.services.shared_instrument_registry import get_shared_instrument

from .conftest import TEST_SHARED_INSTRUMENTS, canonical_quote_policy, seed_shared_instrument


def test_unchanged_directory_reads_do_not_scale_queries_with_members_or_read_prices(client: TestClient):
    from watchlist_app.db.session import get_engine

    assert client.get("/api/watchlists").status_code == 200
    statements: list[str] = []

    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement.lower())

    def read_directory():
        statements.clear()
        event.listen(get_engine(), "before_cursor_execute", capture)
        try:
            response = client.get("/api/watchlists")
        finally:
            event.remove(get_engine(), "before_cursor_execute", capture)
        assert response.status_code == 200
        assert not any(statement.lstrip().startswith(("insert ", "update ", "delete ")) for statement in statements)
        assert not any(
            table in statement
            for statement in statements
            for table in ("instrument_market_data", "corporate_action_event", "fund_nav_event")
        )
        return len(statements), response.json()

    small_count, small = read_directory()
    for position in range(24):
        seed_shared_instrument({
            "instrument_id": f"directory-scale-{position}",
            "instrument_name": f"Directory Scale {position}",
            "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS",
            "quote_selection_policy": canonical_quote_policy("equity"),
            "identifiers": [{"identifier_type": "ticker", "identifier_value": f"DS{position}", "is_primary": True}],
            "market_data": [], "lifecycle_state": {"status": "active"},
        })
    assert client.get("/api/watchlists").status_code == 200
    large_count, large = read_directory()
    assert large_count <= small_count + 2
    assert large[0]["item_count"] == small[0]["item_count"] + 24


def test_directory_reconciles_identity_edits_and_missing_rows_without_membership_changes(client: TestClient):
    assert client.get("/api/watchlists").status_code == 200
    instrument_id = "fund-us-agg"
    with get_session_factory()() as session:
        instrument = session.get(Instrument, instrument_id)
        instrument.instrument_name = "Updated Registry Name"
        instrument.exchange_code = "ARCX"
        instrument.identifiers[0].identifier_value = "UPDATED"
        row = session.scalar(select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == "all-instruments",
            WatchlistRowReadModel.instrument_id == instrument_id,
        ))
        session.delete(row)
        session.commit()
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    with get_session_factory()() as session:
        instrument = session.get(InstrumentDetail, instrument_id)
        assert instrument.instrument_name == "Updated Registry Name"
        assert instrument.primary_identifier_value == "UPDATED"
        assert instrument.metadata_json == {"exchange_code": "ARCX"}
        row = session.scalar(select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == "all-instruments",
            WatchlistRowReadModel.instrument_id == instrument_id,
        ))
        assert row is not None
        assert row.instrument_name == "Updated Registry Name"


def test_directory_type_change_moves_system_membership_and_preserves_current_identity(client: TestClient):
    before = client.get("/api/watchlists").json()
    counts = {item["watchlist_id"]: item["item_count"] for item in before}
    with get_session_factory()() as session:
        instrument = session.get(Instrument, "savf63")
        instrument.instrument_type = "private_fund"
        session.commit()
    response = client.get("/api/watchlists")
    assert response.status_code == 200
    updated = {item["watchlist_id"]: item["item_count"] for item in response.json()}
    assert updated["all-instruments"] == counts["all-instruments"]
    assert updated["all-public-funds"] == counts["all-public-funds"] - 1
    assert updated["all-private-funds"] == counts["all-private-funds"] + 1
    with get_session_factory()() as session:
        instrument = session.get(InstrumentDetail, "savf63")
        assert (instrument.instrument_type, instrument.detail_view_type) == ("private_fund", "private_fund")


def test_directory_default_classification_preserves_manual_assignment_on_exchange_change(client: TestClient):
    instrument_id = "directory-classification"
    seed_shared_instrument({
        "instrument_id": instrument_id, "instrument_name": "Directory Classification",
        "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS",
        "quote_selection_policy": canonical_quote_policy("equity"),
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "DCLASS", "is_primary": True}],
        "market_data": [], "lifecycle_state": {"status": "active"},
    })
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    repository = SQLAlchemyTaxonomyRepository()
    with get_session_factory()() as session:
        assignment = repository.get_assignment(session, instrument_id=instrument_id)
        assert assignment.node_id == "equity-us-unclassified"
        repository.upsert_assignment(session, instrument_id=instrument_id,
            node_id="equity-hk-information-technology", source_record_id="pm-classification")
        session.get(Instrument, instrument_id).exchange_code = "XHKG"
        session.commit()
        history = list(session.scalars(select(InstrumentTaxonomyAssignmentHistory.history_id).where(
            InstrumentTaxonomyAssignmentHistory.instrument_id == instrument_id,
        )))
    assert client.get("/api/watchlists").status_code == 200
    with get_session_factory()() as session:
        assignment = repository.get_assignment(session, instrument_id=instrument_id)
        assert (assignment.node_id, assignment.source_record_id) == ("equity-hk-information-technology", "pm-classification")
        assert list(session.scalars(select(InstrumentTaxonomyAssignmentHistory.history_id).where(
            InstrumentTaxonomyAssignmentHistory.instrument_id == instrument_id,
        ))) == history
        assert session.get(InstrumentDetail, instrument_id).metadata_json == {"exchange_code": "XHKG"}


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
