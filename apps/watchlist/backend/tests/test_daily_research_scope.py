from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from investment_studio_instrument_core.db_models import Instrument

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service, shared_instrument_registry


def test_daily_scope_uses_latest_saved_proposed_or_invested_status_and_reloads_changes(client, monkeypatch):
    monkeypatch.setattr(service, "_research_due", lambda market, now: True)
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


@pytest.mark.parametrize("market,instant,expected", [
    ("cn", "2026-09-08T00:29:00+00:00", False),
    ("cn", "2026-09-08T00:30:00+00:00", True),
    ("hk", "2026-09-08T00:30:00+00:00", True),
    ("us", "2026-09-08T12:29:00+00:00", False),
    ("us", "2026-09-08T12:30:00+00:00", True),
    ("us", "2026-01-06T13:29:00+00:00", False),
    ("us", "2026-01-06T13:30:00+00:00", True),
    ("us", "2026-09-07T12:30:00+00:00", False),  # Labor Day
    ("cn", "2026-10-01T00:30:00+00:00", False),
    ("hk", "2026-07-01T00:30:00+00:00", False),
    ("cn", "2026-09-06T00:30:00+00:00", False),
    ("us", "2026-09-06T12:30:00+00:00", False),
    (None, "2026-09-08T12:30:00+00:00", False),
])
def test_research_is_due_in_market_local_time_only_on_exchange_sessions(market, instant, expected):
    assert service._research_due(market, datetime.fromisoformat(instant)) is expected


def _registered(session, iid, kind, calendar, exchange=None):
    session.add(Instrument(instrument_id=iid, instrument_name=iid, instrument_type=kind, currency="USD",
        exchange_code=exchange, quote_selection_policy_json={}, source_settings_json={"market_calendar": calendar},
        lifecycle_state_json={"status": "active"}))
    session.add(InstrumentDetail(instrument_id=iid, instrument_type=kind, detail_view_type=kind,
        instrument_name=iid, is_active=True, metadata_json={}))
    session.flush()
    session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status", value_json="Invested",
        adopted_at=datetime.now(UTC)))


def test_daily_scope_uses_registered_calendar_and_keeps_private_funds_in_domestic_morning(client, monkeypatch):
    ids = ["daily-cn", "daily-hk", "daily-us", "daily-private", "daily-index"]
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ids)
    with get_session_factory()() as session:
        _registered(session, "daily-cn", "etf", "XSHE", "XSHE")
        _registered(session, "daily-hk", "equity", "XHKG", "XHKG")
        _registered(session, "daily-us", "equity", None, "ARCX")
        _registered(session, "daily-private", "private_fund", None)
        _registered(session, "daily-index", "index", None)
        session.commit()
        morning = datetime.fromisoformat("2026-09-08T00:30:00+00:00")
        assert service.daily_review_groups(session, now=morning) == [[iid] for iid in ["daily-cn", "daily-hk", "daily-private"]]
        us_morning = datetime.fromisoformat("2026-09-08T12:30:00+00:00")
        assert service.daily_review_groups(session, now=us_morning) == [[iid] for iid in ["daily-cn", "daily-hk", "daily-private", "daily-us"]]


def test_scheduled_us_review_deduplicates_across_beijing_midnight_and_reopens_next_us_day(client, monkeypatch):
    class Clock:
        current = datetime.fromisoformat("2026-09-08T12:30:00+00:00")
        @classmethod
        def now(cls, tz):
            return cls.current.astimezone(tz)
        fromisoformat = datetime.fromisoformat

    monkeypatch.setattr(service, "datetime", Clock)
    with get_session_factory()() as session:
        _registered(session, "daily-us", "equity", "XNAS", "XNAS")
        session.commit()
        first, created = service.begin_run(session, ["daily-us"], scheduled=True)
        assert created
        first.status = "completed"
        session.commit()
        Clock.current = datetime.fromisoformat("2026-09-09T01:00:00+00:00")
        same, created = service.begin_run(session, ["daily-us"], scheduled=True)
        assert not created and same.entry_id == first.entry_id
        Clock.current = datetime.fromisoformat("2026-09-09T12:30:00+00:00")
        next_day, created = service.begin_run(session, ["daily-us"], scheduled=True)
        assert created and next_day.entry_id != first.entry_id


def test_worker_does_not_read_portfolio_or_run_risk_when_no_market_research_is_due(client, monkeypatch):
    from watchlist_app.services import research_workbench
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: pytest.fail("No due market scope"))
    service.run_daily_reviews(Event())
