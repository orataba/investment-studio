"""Exercise retained JSON, successful coverage and batch access in a disposable DB."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from watchlist_app.services.research_identity import research_identity

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_research_refresh_batch import batch_module, ready_notebook


pytestmark = pytest.mark.postgresql_integration


def add_instrument_detail(session, iid):
    session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
        instrument_name="Recovery fixture", is_active=True, metadata_json={}))
    session.flush()


def test_retry_preserves_retained_inputs_and_only_published_coverage_advances_clock(postgres_watchlist_env, monkeypatch):
    class Clock:
        current = datetime(2026, 9, 24, 12, tzinfo=UTC)
        @classmethod
        def now(cls, tz):
            return cls.current.astimezone(tz)
        fromisoformat = datetime.fromisoformat
    monkeypatch.setattr(sector_research, "datetime", Clock)
    iid, now = postgres_watchlist_env["instrument_id"], Clock.current
    topic_id = sector_research.INSTRUMENT_TOPIC_PREFIX + iid
    good_cutoff = (now - timedelta(days=1)).isoformat()
    original = {"sector_run": True, "instrument_ids": [iid], "cutoff": (now - timedelta(minutes=5)).isoformat(),
        "research_actor": research_identity(), "last_successful_review_cutoff": good_cutoff,
        "submitted_draft": {"events": [], "memo": "original draft"}, "retained_source": "untouched\x00original",
        "runtime_error": {"type": "ProviderUnavailable", "retryable": True}, "execution": {"attempt": 1}}
    with get_session_factory()() as session:
        add_instrument_detail(session, iid)
        session.add(ResearchTopic(topic_id=topic_id, title="Recovery", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="published", topic_id=topic_id, kind="analysis", title="Published", status="completed",
            created_at=now - timedelta(days=1), completed_at=now - timedelta(days=1),
            context_json={"sector_run": True, "instrument_ids": [iid], "cutoff": good_cutoff, "retained_source": "original\x00text"}))
        session.add(ResearchEntry(entry_id="annotation", topic_id=topic_id, kind="analysis", title="Annotation", status="completed",
            created_at=now - timedelta(hours=1), completed_at=now - timedelta(hours=1),
            context_json={"sector_run": True, "recordkeeping_only": True, "instrument_ids": [iid], "cutoff": now.isoformat()}))
        session.add(ResearchEntry(entry_id="failed", topic_id=topic_id, kind="analysis", title="Failed", status="failed",
            created_at=now - timedelta(minutes=5), completed_at=now - timedelta(minutes=2), context_json=deepcopy(original)))
        session.commit()
        recovered, created = sector_research.begin_run(session, [iid], scheduled=True)
        assert created and recovered.entry_id == "failed" and recovered.status == "queued"
        for key in ("submitted_draft", "retained_source", "cutoff", "last_successful_review_cutoff"):
            assert recovered.context_json[key] == original[key]
        recovered.status = "failed"
        recovered.completed_at = now
        recovered.context_json = {**recovered.context_json, "runtime_error": {"type": "ModelUnavailable"}}
        session.commit()
        new, created = sector_research.begin_run(session, [iid], scheduled=False)
        assert created and new.entry_id not in {"failed", "published", "annotation"}
        assert new.context_json["last_successful_review_cutoff"] == good_cutoff
        assert session.get(ResearchEntry, "published").context_json["retained_source"] == "original\x00text"


def test_batch_run_projection_retains_no_change_report_and_checks_original_access(postgres_watchlist_env):
    iid = postgres_watchlist_env["instrument_id"]
    topic_id = sector_research.INSTRUMENT_TOPIC_PREFIX + iid
    now = datetime.now(UTC)
    notebook = ready_notebook()
    notebook["investment_view"]["version_id"] = "view-version"
    notebook["decision_brief"]["basis_view_version_id"] = "view-version"
    with get_session_factory()() as session:
        add_instrument_detail(session, iid)
        session.add_all([ResearchTopic(topic_id=topic_id, title="Readable", visibility="team"),
                         ResearchTopic(topic_id="other-team", title="Other", visibility="team", team_id="other")])
        session.flush()
        baseline = {"sector_run": True, "instrument_ids": [iid], "cutoff": (now - timedelta(days=1)).isoformat(),
                    "reviews": {iid: {"status": "limited", "research": notebook}}, "retained_source": "original\x00document"}
        quiet = {"sector_run": True, "instrument_ids": [iid], "cutoff": now.isoformat(),
                 "reviews": {iid: {"status": "limited", "change_kind": "no_change"}}, "retained_source": "original\x00document"}
        for entry_id, topic, team, offset, context in [
            ("baseline", topic_id, "default", -1, baseline),
            ("quiet", topic_id, "default", 0, quiet),
            ("foreign", "other-team", "other", 1, quiet),
        ]:
            session.add(ResearchEntry(entry_id=entry_id, topic_id=topic, team_id=team, kind="analysis", title=entry_id,
                status="completed", created_at=now + timedelta(days=offset), completed_at=now + timedelta(days=offset), context_json=context))
        session.commit()
    batch = batch_module()
    result = batch.result_record(iid, "quiet", batch.run_state("quiet", iid))
    assert result["published"] and result["report_ready"] and result["change_kind"] == "no_change"
    with pytest.raises(ValueError, match="selected instrument"):
        batch.run_state("quiet", "different-instrument")
    with pytest.raises(HTTPException) as denied:
        batch.run_state("foreign", iid)
    assert denied.value.status_code == 404
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "quiet").context_json["retained_source"] == "original\x00document"
