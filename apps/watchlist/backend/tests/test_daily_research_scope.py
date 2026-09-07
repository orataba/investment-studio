from datetime import UTC, datetime, timedelta

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service, shared_instrument_registry


def test_daily_scope_uses_latest_saved_proposed_or_invested_status_and_reloads_changes(client, monkeypatch):
    rows = [
        ("proposed", "etf", True, "Proposed"),
        ("invested", "private_fund", True, "Invested"),
        ("watch", "equity", True, "Watch"),
        ("paused", "public_fund", True, "Paused"),
        ("exited", "index", True, "Exited"),
        ("unset", "equity", True, None),
        ("inactive", "equity", False, "Invested"),
        ("not-registered", "equity", True, "Invested"),
        ("unsupported", "bond", True, "Invested"),
    ]
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids",
        lambda **kwargs: [iid for iid, *_ in rows if iid != "not-registered"])
    with get_session_factory()() as session:
        for iid, kind, active, status in rows:
            session.add(InstrumentDetail(instrument_id=iid, instrument_type=kind, detail_view_type=kind,
                instrument_name=iid, is_active=active, metadata_json={}))
            session.flush()
            if status:
                session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
                    value_json=status, adopted_at=datetime.now(UTC) - timedelta(days=1)))
        session.commit()
        assert service.daily_review_groups(session) == [["invested"], ["proposed"]]

    # Use the same endpoint as Watchlist settings; old attribute history remains saved.
    for iid, status in [("proposed", "Watch"), ("watch", "Proposed"), ("invested", "Exited")]:
        response = client.post(f"/api/instrument-attributes/instruments/{iid}",
            json={"values": [{"attribute_key": "coverage_status", "value": status}]})
        assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        assert service.daily_review_groups(session) == [["watch"]]
        # Leaving automatic scope does not prevent an explicit single-instrument check.
        run, created = service.begin_run(session, ["proposed"])
        assert created and run.context_json["instrument_ids"] == ["proposed"]
