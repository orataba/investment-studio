from watchlist_app.services import risk_review_state
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from investment_studio_instrument_core.db_models import Instrument

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service, shared_instrument_registry


def test_daily_scope_uses_latest_saved_proposed_or_invested_status_and_reloads_changes(client, monkeypatch):
    monkeypatch.setattr(service, "_research_due", lambda market, now: True)
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "us")
    rows = [
        ("proposed", "etf", True, "Proposed"),
        ("invested", "equity", True, "Invested"),
        ("private-invested", "private_fund", True, "Invested"),
        ("public-proposed", "public_fund", True, "Proposed"),
        ("crypto-invested", "crypto", True, "Invested"),
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
        assert service.daily_review_groups(session) == [[iid] for iid in ["crypto-invested", "invested", "private-invested", "proposed", "public-proposed"]]

    # Use the same endpoint as Watchlist settings; old attribute history remains saved.
    for iid, status in [("proposed", "Watch"), ("watch", "Proposed"), ("invested", "Exited")]:
        response = client.post(f"/api/instrument-attributes/instruments/{iid}",
            json={"values": [{"attribute_key": "coverage_status", "value": status}]})
        assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        assert service.daily_review_groups(session) == [[iid] for iid in ["crypto-invested", "private-invested", "public-proposed", "watch"]]
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
    ("fund_nav", "2026-09-08T00:29:00+00:00", False),
    ("fund_nav", "2026-09-08T00:30:00+00:00", True),
    ("fund_nav", "2026-09-06T00:30:00+00:00", True),
    ("crypto", "2026-09-06T00:29:00+00:00", False),
    ("crypto", "2026-09-06T00:30:00+00:00", True),
    ("crypto", "2026-10-01T00:30:00+00:00", True),
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


def test_daily_scope_uses_registered_calendar_and_explicit_fund_nav_check_clock(client, monkeypatch):
    ids = ["daily-cn", "daily-hk", "daily-us", "daily-private", "daily-public", "daily-index", "daily-unknown", "daily-private-us"]
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ids)
    monkeypatch.setattr(service, "latest_reviews", lambda session, **kwargs: {iid: {} for iid in ids})
    with get_session_factory()() as session:
        _registered(session, "daily-cn", "etf", "XSHE", "XSHE")
        _registered(session, "daily-hk", "equity", "XHKG", "XHKG")
        _registered(session, "daily-us", "equity", None, "ARCX")
        _registered(session, "daily-private", "private_fund", None)
        _registered(session, "daily-public", "public_fund", "XSHG")
        _registered(session, "daily-private-us", "private_fund", "XNYS")
        _registered(session, "daily-index", "index", "XSHG")
        _registered(session, "daily-unknown", "index", None)
        session.commit()
        assert service._research_market(session, "daily-private") == "fund_nav"
        assert service._research_market(session, "daily-private-us") == "us"
        morning = datetime.fromisoformat("2026-09-08T00:30:00+00:00")
        assert service.daily_review_groups(session, now=morning) == [[iid] for iid in ["daily-cn", "daily-hk", "daily-index", "daily-private", "daily-public"]]
        us_morning = datetime.fromisoformat("2026-09-08T12:30:00+00:00")
        assert service.daily_review_groups(session, now=us_morning) == [[iid] for iid in ["daily-cn", "daily-hk", "daily-index", "daily-private", "daily-private-us", "daily-public", "daily-us"]]


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


def test_crypto_uses_completed_utc_day_clock_and_keeps_weekends_in_automatic_scope(client, monkeypatch):
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ["btcusd"])
    monkeypatch.setattr(service, "latest_reviews", lambda session, **kwargs: {"btcusd": {}})
    with get_session_factory()() as session:
        _registered(session, "btcusd", "crypto", None)
        session.commit()
        assert service._research_market(session, "btcusd") == "crypto"
        before = datetime.fromisoformat("2026-09-06T00:29:00+00:00")
        after = datetime.fromisoformat("2026-09-06T00:30:00+00:00")
        assert service.daily_review_groups(session, now=before) == []
        assert service.daily_review_groups(session, now=after) == [["btcusd"]]
        assert service._research_dates(session, ["btcusd"], datetime.fromisoformat("2026-09-06T16:30:00+00:00")) == {"btcusd": "2026-09-06"}
        run, created = service.begin_run(session, ["btcusd"])
        assert created and run.context_json["instrument_ids"] == ["btcusd"]


def test_worker_does_not_read_portfolio_or_run_risk_when_no_market_research_is_due(client, monkeypatch):
    from watchlist_app.services import research_workbench
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: pytest.fail("No due market scope"))
    service.run_daily_reviews(Event())


def test_registered_catalogue_does_not_schedule_list_risk_but_invested_funds_still_run(client, monkeypatch):
    from watchlist_app.db.models import Watchlist
    from watchlist_app.services import research_runner, research_workbench, risk_officer

    with get_session_factory()() as session:
        _registered(session, "registered-fund", "private_fund", None)
        if session.get(Watchlist, "all-instruments") is None:
            session.add(Watchlist(watchlist_id="all-instruments", name="全部已登记标的", owner_type="system", owner_id="system", sort_order=0))
        session.commit()
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [["registered-fund"]])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    risk_scopes, research_ids = [], []

    def read_scope(session, scope):
        risk_scopes.append(scope)
        return {"instrument_ids": []}

    def analyze(run_id):
        with get_session_factory()() as session:
            run = session.get(service.ResearchEntry, run_id)
            research_ids.extend(run.context_json["instrument_ids"])
            run.status = "completed"
            session.commit()

    monkeypatch.setattr(risk_review_state, "current_scope", read_scope)
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    assert research_ids == ["registered-fund"]
    assert {"watchlist_id": "all-instruments"} not in risk_scopes


def test_new_proposed_instrument_initializes_on_weekend_once_and_retains_seven_day_window(client, monkeypatch):
    from watchlist_app.services import research_runner, research_workbench, risk_officer
    now = datetime(2026, 9, 27, 1, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ["new-proposed"])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_review_state, "current_scope", lambda session, scope: {"instrument_ids": []})
    with get_session_factory()() as session:
        _registered(session, "new-proposed", "equity", "XNAS", "XNAS")
        session.flush()
        status = session.query(InstrumentAttributeValue).filter_by(instrument_id="new-proposed").one()
        status.value_json = "Proposed"
        session.commit()
        assert service._research_due("us", now) is False
        assert service.daily_review_groups(session, now=now) == [["new-proposed"]]
    dispatched = []
    def analyze(run_id):
        dispatched.append(run_id)
        service.prepare_run(run_id)
        with get_session_factory()() as session:
            run = session.get(service.ResearchEntry, run_id)
            initial = run.context_json["initialization"]
            assert datetime.fromisoformat(initial["as_of"]) - datetime.fromisoformat(initial["published_after"]) == timedelta(days=7)
            assert initial["as_of"] == run.context_json["input_snapshot_cutoff"]
            # A deterministic acquisition fault does not authorize continuous
            # new initialization jobs outside the ordinary market schedule.
            run.status = "failed"
            run.completed_at = now
            session.commit()
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    service.run_daily_reviews(Event())
    service.run_daily_reviews(Event())
    assert len(dispatched) == 1
    with get_session_factory()() as session:
        assert service.daily_review_groups(session, now=now) == []


@pytest.mark.parametrize("aggregate", ["none", "watchlist", "portfolio", "portfolio-and-watchlist"])
@pytest.mark.parametrize("case_status", ["recorded", "resolved", "handled"])
def test_pending_research_referrals_reach_officer_outside_market_hours_without_duplicate_scope(client, monkeypatch, aggregate, case_status):
    from types import SimpleNamespace
    from watchlist_app.db.models import Watchlist
    from watchlist_app.db.models.workbench import RiskCase
    from watchlist_app.services import research_runner, research_workbench, risk_officer
    monkeypatch.setattr(service, "daily_review_groups", lambda session: [])
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ["referral"])
    in_portfolio = "portfolio" in aggregate
    in_watchlist = "watchlist" in aggregate
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {
        "portfolios": [{"portfolio_id": "held"}] if in_portfolio else []})
    monkeypatch.setattr(risk_review_state, "current_scope", lambda session, scope: {"instrument_ids": ["referral"]
        if scope.get("portfolio_id") == "held" or scope.get("watchlist_id") == "named-list" else []})
    with get_session_factory()() as session:
        _registered(session, "referral", "equity", "XNAS", "XNAS")
        if in_watchlist:
            session.add(Watchlist(watchlist_id="named-list", name="Shared research", owner_type="user", owner_id="pm"))
        session.add(RiskCase(case_id="pending-referral", instrument_id="referral", signal="sector:development",
            title="Material new evidence", status=case_status, trigger_active=False,
            evidence_json={"risk_assessment": {"status": "pending"}}))
        session.commit()
    scopes = []
    def begin_risk(session, **scope):
        scopes.append(scope)
        return SimpleNamespace(entry_id="officer", status="completed"), False
    monkeypatch.setattr(risk_officer, "begin_run", begin_risk)
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args: pytest.fail("Referral must not launch a fresh instrument investigation"))
    service.run_daily_reviews(Event())
    expected = ([{"portfolio_id": "held"}] if in_portfolio else []) + [
        {"watchlist_id": "named-list"} if in_watchlist else {"instrument_id": "referral"}]
    assert [{key: value for key, value in scope.items() if key != "scheduled_dates"} for scope in scopes] == expected
    assert all(set(scope["scheduled_dates"]) == {"referral"} for scope in scopes)


def test_first_published_research_referral_is_processed_in_same_worker_pass(client, monkeypatch):
    from types import SimpleNamespace
    from watchlist_app.db.models.workbench import RiskCase
    from watchlist_app.services import research_runner, research_workbench, risk_officer
    monkeypatch.setattr(shared_instrument_registry, "list_shared_active_instrument_ids", lambda **kwargs: ["first-referral"])
    monkeypatch.setattr(research_workbench, "portfolio_options", lambda: {"portfolios": []})
    monkeypatch.setattr(risk_review_state, "current_scope", lambda session, scope: {"instrument_ids": []})
    with get_session_factory()() as session:
        _registered(session, "first-referral", "equity", "XNAS", "XNAS")
        session.commit()
    order = []
    def analyze(run_id):
        with get_session_factory()() as session:
            session.get(service.ResearchEntry, run_id).status = "completed"
            session.add(RiskCase(case_id="first-risk", instrument_id="first-referral", signal="sector:development",
                title="Material new risk", trigger_active=False, evidence_json={"risk_assessment": {"status": "pending"}}))
            session.commit()
        order.append("research")
    def begin_risk(session, **scope):
        assert scope["instrument_id"] == "first-referral"
        assert order == ["research"]
        order.append("officer")
        return SimpleNamespace(entry_id="officer", status="completed"), False
    monkeypatch.setattr(research_runner, "run_analysis", analyze)
    monkeypatch.setattr(risk_officer, "begin_run", begin_risk)
    service.run_daily_reviews(Event())
    assert order == ["research", "officer"]
