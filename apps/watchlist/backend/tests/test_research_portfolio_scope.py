"""Historical portfolio permissions survive the indexed candidate filter."""
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.research_scope import RESEARCH_PORTFOLIO_SCOPE_FUNCTION_DDL
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_access


def test_scope_keeps_current_and_all_historical_entry_kinds(client, monkeypatch):
    contexts = {
        "note": {"portfolio_id": "old-note"},
        "evidence": {"risk_scope": {"portfolio_id": "old-evidence"}},
        "analysis": {"portfolio_id": "old-failed", "instrument_ids": ["unrelated-instrument"]},
        "literal": {"portfolio_id": r"literal\u0000", "source": "original\x00text"},
        "empty": {"portfolio_id": "", "risk_scope": {"portfolio_id": None}},
    }
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="scope-history", title="History", visibility="team",
                              portfolio_id="current")
        public = ResearchTopic(topic_id="scope-public", title="Public", visibility="team")
        session.add_all([topic, public])
        session.flush()
        for key, context in contexts.items():
            session.add(ResearchEntry(entry_id=key, topic_id=topic.topic_id,
                kind=key if key in {"note", "evidence", "analysis"} else "note", title=key,
                status="failed", context_json=context))
        session.add(ResearchEntry(entry_id="public", topic_id=public.topic_id, kind="note",
            title="Public", context_json={"source": "unscoped\x00original"}))
        session.commit()
        before = {key: deepcopy(session.get(ResearchEntry, key).context_json) for key in contexts}

        expected = {"current", "old-note", "old-evidence", "old-failed", r"literal\u0000"}
        assert research_access.topic_portfolio_ids_by_topic(session, [topic, public]) == {
            "scope-history": expected, "scope-public": set(),
        }
        checked = []
        monkeypatch.setattr(research_access, "require_portfolio", checked.append)
        assert research_access.require_topic_access(session, topic) is topic
        assert set(checked) == expected

        def deny_old_scope(portfolio_id):
            if portfolio_id == "old-evidence":
                raise HTTPException(404, "Retained portfolio is not accessible")
        monkeypatch.setattr(research_access, "require_portfolio", deny_old_scope)
        with pytest.raises(HTTPException) as denied:
            research_access.require_topic_access(session, topic)
        assert denied.value.status_code == 404
        assert {key: session.get(ResearchEntry, key).context_json for key in contexts} == before


def test_portfolio_scope_ddl_matches_its_frozen_migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260923_0063_research_retained_portfolio_scope.py"
    spec = importlib.util.spec_from_file_location("retained_portfolio_scope_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.FUNCTION_DDL == RESEARCH_PORTFOLIO_SCOPE_FUNCTION_DDL
