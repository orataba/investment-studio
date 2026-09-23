"""Research views read a scope once, without per-theme or per-member SQL growth."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from watchlist_app.db.models import InstrumentDetail, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_activity, research_dossier, risk_officer, sector_research


def _counted_read(callback):
    with get_session_factory()() as session:
        statements = []
        event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))
        result = callback(session)
        return result, len(statements)


def test_dossier_history_is_read_once_for_all_themes_and_agenda(client):
    iid = "xlk"
    now = datetime(2026, 9, 20, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="etf", detail_view_type="etf",
            instrument_name="XLK", metadata_json={}))
        session.add_all([ResearchTopic(topic_id=topic, title=topic, visibility="team", instrument_ids=[iid])
                         for topic in ("dossier:xlk", "published", "historical-portfolio")])
        session.flush()
        session.add(ResearchEntry(entry_id="old-private-scope", topic_id="historical-portfolio", title="Old scope",
            kind="note", context_json={"portfolio_id": "private-original"}))
        session.add(ResearchEntry(entry_id="private-notebook", topic_id="historical-portfolio", title="Private",
            kind="analysis", status="completed", created_at=now + timedelta(days=1),
            context_json={"research_run": True, "instrument_ids": [iid], "reviews": {iid: {"status": "completed",
                "research": {"investment_view": {"direction": "Never disclose"}, "questions": []}}}}))
        session.commit()

    counts = []
    for count in (1, 8):
        with get_session_factory()() as session:
            for index in range(count):
                key = f"theme-{index}"
                if session.get(ResearchEntry, key) is None:
                    session.add(ResearchEntry(entry_id=key, topic_id="dossier:xlk", kind="note", title=key,
                        context_json={"role": "research_theme", "instrument_id": iid, "author": "PM",
                            "theme_status": "active"}))
            for revision, stamp in enumerate((now, now + timedelta(hours=1), now + timedelta(hours=2))):
                questions = [{"key": f"question-{index}", "theme_id": f"theme-{index}",
                    "version_id": f"question-{index}:v{min(revision, 1)}", "question": f"Question {index}",
                    "assessment": f"Assessment {min(revision, 1)}", "status": "open", "source_ids": [],
                    "next_check": "Next disclosure", "evidence_for": [], "evidence_against": []} for index in range(count)]
                entry = session.get(ResearchEntry, f"published-{revision}")
                if entry is None:
                    entry = ResearchEntry(entry_id=f"published-{revision}", topic_id="published", kind="analysis",
                        title="Notebook", status="completed", created_at=stamp, completed_at=stamp)
                    session.add(entry)
                entry.context_json = {"research_run": True, "instrument_ids": [iid], "cutoff": stamp.isoformat(),
                    "reviews": {iid: {"status": "completed", "research": {"version_id": f"notebook-{revision}",
                        "questions": questions, "sources": []}, "reflection": {"status": "reviewed",
                        "reviewed_update_ids": [f"research:question-{index}:v1" for index in range(count)]}}}}
            session.commit()
        dossier, queries = _counted_read(lambda session: research_dossier.read_dossier(session, iid))
        counts.append(queries)
        assert len(dossier["themes"]) == count
        assert len(dossier["review_agenda"]["tracked_questions"]) == count
        assert dossier["notebook"]["version_id"] == "notebook-2"
        for theme in dossier["themes"]:
            assert [row["assessment"] for row in theme["research_progress"]] == ["Assessment 0", "Assessment 1"]
            assert theme["current_questions"][0]["last_reviewed_at"] == (now + timedelta(hours=2)).isoformat()
    assert counts[0] == counts[1], f"Theme count must not multiply historical reads: {counts}"


def test_risk_snapshot_batches_missing_and_present_member_reads(client):
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="bulk-risk", name="Bulk risk", owner_type="team", owner_id="default"))
        session.commit()
    counts = []
    for count in (1, 25):
        with get_session_factory()() as session:
            for index in range(count):
                iid = f"bulk-fund-{index}"
                if session.get(InstrumentDetail, iid) is None:
                    session.add(InstrumentDetail(instrument_id=iid, instrument_type="private_fund", detail_view_type="fund",
                        instrument_name=iid, metadata_json={}))
                    session.flush()
                    session.add(WatchlistItem(watchlist_id="bulk-risk", instrument_id=iid, added_at=datetime.now(UTC)))
            session.commit()
        snapshot, queries = _counted_read(lambda session: risk_officer.read_snapshot(session, watchlist_id="bulk-risk"))
        counts.append(queries)
        assert len(snapshot["instruments"]) == count
        assert all(not row["performance_evidence"]["available"] for row in snapshot["instruments"])
        assert all(row["research_context"]["current_stance"] is None for row in snapshot["instruments"])
    assert counts[0] == counts[1], f"Scope size must not multiply individual record lookups: {counts}"


def test_equal_research_clocks_have_inverse_stable_history_order(client):
    stamp = datetime(2026, 9, 20, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="equal-clock", title="Equal clock", visibility="team", instrument_ids=["xlk"]))
        session.flush()
        for key in ("first", "last"):
            session.add(ResearchEntry(entry_id=key, topic_id="equal-clock", kind="analysis", title=key,
                status="completed", created_at=stamp, completed_at=stamp, context_json={"research_run": True,
                    "instrument_ids": ["xlk"], "reviews": {"xlk": {"status": "completed",
                    "research": {"investment_view": {"direction": key}}, "reflection": {"status": "reviewed",
                    "summary": key, "reviewed_update_ids": ["research:same-judgment"]}}}}))
        session.commit()
    with get_session_factory()() as session:
        ascending = [row.entry_id for row, _ in research_dossier._research_records(session, "xlk", oldest_first=True)]
        descending = [row.entry_id for row, _ in research_dossier._research_records(session, "xlk")]
        assert ascending == ["first", "last"]
        assert ascending == list(reversed(descending))
        assert research_dossier._notebooks(session, "xlk", False)[0]["investment_view"]["direction"] == "last"
        assert sector_research.review_states(session, instrument_ids=["xlk"])["latest"]["xlk"]["current_summary"] == "last"
        assert research_activity.review_receipts(session, "xlk")["research:same-judgment"]["last_review_summary"] == "last"
