"""JSON event publication/read projection on the disposable PostgreSQL fixture."""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_activity import event_page
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_pg_event_publish_and_nul_safe_projection_preserve_history_and_risk(postgres_watchlist_env, monkeypatch):
    iid = postgres_watchlist_env["instrument_id"]
    monkeypatch.setattr(service, "_research_market", lambda session, iid: "fund_nav")
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund", instrument_name="Test Fund", metadata_json={}))
        session.commit()
        run, _ = service.begin_run(session, [iid])
        source = {"source_id": "event-original", "url": "https://example.test/notice", "title": "Test notice", "text": "Original\x00body",
            "published_at": "2026-01-01", "time_status": "date_only"}
        run.context_json = {**run.context_json, "web_evidence": [{"operation": "fetch", "sources": [source]}]}
        event = {"event_key": "terms-update", "action": "new", "direction": "risk", "title": "条款变化", "body": "留存条款证据待独立复核",
            "next_watch": "下一次正式条款披露", "confidence": "reported", "information_type": "fact", "recording_type": "backfill",
            "published_at": "2026-01-01", "source_ids": [source["source_id"]], "importance_score": 4, "importance_reason": "条款变化可能影响赎回安排。"}
        service.apply_result(session, run, json.dumps({"reviews": [{"instrument_id": iid, "events": [event]}]}))
        session.commit()
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == iid))
        original_history = list(case.history_json)
        assert case.evidence_json["follow_up_until"] and not case.trigger_active
        case.trigger_active, case.status = True, "investigating"
        case.evidence_json = {**case.evidence_json, "importance_reason": "条款\x00需复核"}
        session.commit()
        page = event_page(session, iid, scope="watch")
        assert page["events"][0]["importance_reason"] == "条款\x00需复核"
        assert page["events"][0]["sources"][0]["title"] == "Test notice"
        # Projection neither mutates originals nor loses an actual NUL in a selected field.
        session.refresh(case)
        assert case.history_json == original_history
        assert case.evidence_json["sources"][0]["text"] == "Original\x00body"
        case.evidence_json = {key: value for key, value in case.evidence_json.items() if key != "source_views"}
        session.commit()
        assert event_page(session, iid, scope="watch")["events"][0]["sources"][0]["title"] == "Test notice"
        # Starting new work must not invent dates for unknown legacy terms or
        # mutate an already assessed risk.
        case.evidence_json = {key: value for key, value in case.evidence_json.items() if key not in {"follow_up_until", "follow_up_started_at"}}
        session.commit()
        before_evidence, before_history = dict(case.evidence_json), list(case.history_json)
        service.begin_run(session, [iid])
        session.refresh(case)
        assert case.evidence_json == before_evidence and case.history_json == before_history
        assert case.trigger_active and case.status == "investigating"
        history = event_page(session, iid, scope="history", limit=1)
        assert history["total"] == 1 and len(history["events"]) == 1


@pytest.mark.parametrize('writer', ['pm_follow_up', 'automatic_refresh'])
def test_pg_risk_writes_wait_and_preserve_concurrent_history(postgres_watchlist_env, writer):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from watchlist_app.api.routes.workbench import follow_up, RiskFollowUp
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    iid = postgres_watchlist_env['instrument_id']
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund', instrument_name='Concurrent risk', metadata_json={}))
        session.flush()
        session.add(RiskCase(case_id='concurrent-risk', instrument_id=iid, signal='drawdown_limit', title='Risk', body='Original',
            status='open', trigger_active=True, evidence_json={}, history_json=[]))
        session.commit()
    read = Event()
    def update():
        with get_session_factory()() as session:
            cached = session.get(RiskCase, 'concurrent-risk')
            assert cached.history_json == []
            read.set()
            if writer == 'pm_follow_up':
                follow_up('concurrent-risk', RiskFollowUp(status='investigating', note='Second note'), session)
            else:
                refresh_risk_cases(session, [iid])
                session.commit()
    with get_session_factory()() as first, ThreadPoolExecutor(max_workers=1) as pool:
        case = first.get(RiskCase, 'concurrent-risk', with_for_update=True)
        future = pool.submit(update)
        assert read.wait(timeout=5)
        with pytest.raises(TimeoutError):
            future.result(timeout=0.1)
        case.history_json = [{'at': datetime.now(UTC).isoformat(), 'action': 'investigating', 'detail': 'First PM note'}]
        case.status = 'investigating'
        first.commit()
        future.result(timeout=5)
    with get_session_factory()() as session:
        saved = session.get(RiskCase, 'concurrent-risk')
        assert saved.history_json[0]['detail'] == 'First PM note'
        assert len(saved.history_json) == 2
        assert saved.status == 'investigating'
        assert saved.history_json[1]['action'] == ('investigating' if writer == 'pm_follow_up' else 'cleared')


def test_pg_concurrent_first_risk_refresh_creates_one_case(postgres_watchlist_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    iid, ready = postgres_watchlist_env['instrument_id'], Barrier(2)
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund', instrument_name='First risk', metadata_json={}))
        session.commit()
    def refresh():
        with get_session_factory()() as session:
            ready.wait(timeout=5)
            refresh_risk_cases(session, [iid])
            session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(refresh) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    with get_session_factory()() as session:
        assert len(list(session.scalars(select(RiskCase).where(RiskCase.instrument_id == iid, RiskCase.signal == 'data_coverage')))) == 1


def test_pg_working_set_filters_before_loading_nul_sources_and_monitor_is_transactional(postgres_watchlist_env, monkeypatch):
    from copy import deepcopy
    from sqlalchemy import event
    from watchlist_app.db.models.research_monitor import ResearchMonitorState
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.services.research_dossier import latest_working_set
    from watchlist_app.services import research_working_set, research_monitor, research_triggers
    iid = postgres_watchlist_env["instrument_id"]
    now = datetime.now(UTC)
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund", instrument_name="Monitor", metadata_json={}))
        session.flush()
        session.add_all([RiskCase(case_id=key, instrument_id=iid, signal=f"sector:{key}", title=key, body=key,
            status="recorded", trigger_active=False, evidence_json={"follow_up": follow, "next_watch": "准确\x00原文",
                "sources": [{"source_id": key, "text": "Original\x00" * 20000}]}, history_json=[])
            for key, follow in (("watch", "watch"), ("closed", "none"))])
        session.commit()
        assert session.get(ResearchMonitorState, ("default", iid)) is None  # 0070 does not invent previous coverage.
        session.expunge_all()
        loaded = []
        event.listen(session, "loaded_as_persistent", lambda _s, obj: loaded.append(obj.case_id) if isinstance(obj, RiskCase) else None)
        selected = research_working_set.current_events(session, iid)
        assert loaded == ["watch"] and selected[0]["next_watch"] == "准确\x00原文"
        assert selected[0]["evidence"]["sources"][0]["text"].count("\x00") == 20000
        monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: {"reasons": [], "coverage_cursor": {}})
        state, result = research_monitor.scan(session, iid, {}, team_id="default", now=now)
        assert result is None
        session.commit()
        before = deepcopy(state.scan_cursor_json)
        state.scan_cursor_json = {"should_rollback": True}
        session.flush()
        session.rollback()
        assert state.scan_cursor_json == before and state.applied_at is None
        topic_id = f"instrument-events:{iid}"
        session.add(ResearchTopic(topic_id=topic_id, title="Shared monitor", visibility="team", team_id="default"))
        session.flush()
        working = {"selected_theme_ids": ["主题\x00原值"], "reviewed_theme_ids": [], "cutoff": now.isoformat()}
        session.add(ResearchEntry(entry_id="completed-monitor", topic_id=topic_id, team_id="default",
            kind="analysis", title="Published monitor", status="completed", completed_at=now,
            context_json={"sector_run": True, "instrument_ids": [iid], "research_working_sets": {iid: working},
                "unrelated_history": "Old original\x00" * 20000}))
        session.commit()
        assert latest_working_set(session, iid, actor={"team_id": "default"}) == working
