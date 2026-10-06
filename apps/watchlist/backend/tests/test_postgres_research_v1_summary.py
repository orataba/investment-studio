"""V1 card projection preserves PostgreSQL JSON provenance and team scope."""
from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_themes import ThemeInput, save_theme, theme_record, theme_summaries
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env


pytestmark = pytest.mark.postgresql_integration


def test_theme_cards_do_not_fetch_retained_archives_or_foreign_team(postgres_watchlist_env):
    iid = postgres_watchlist_env["instrument_id"]
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
                                    instrument_name="V1 fictional test instrument", is_active=True, metadata_json={}))
        session.flush()
        theme = save_theme(session, iid, ThemeInput(title="虚构产品叙事主题", synthesis="已保存的综合认识"))
        entry = session.get(ResearchEntry, theme["theme_id"])
        entry.context_json = {**entry.context_json, "versions": [{"text": "old\x00original" * 2000}], "synthesis": "exact\x00synthesis"}
        original = deepcopy(entry.context_json)
        session.add(ResearchEntry(entry_id="other-team-theme", topic_id=entry.topic_id, kind="note", title="Private theme",
                                  team_id="other", context_json={**original, "synthesis": "must not leak"}))
        session.commit()
    with get_session_factory()() as session:
        statements = []
        event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))
        result = theme_summaries(session, iid)
        assert len(result["themes"]) == 1
        assert result["themes"][0]["synthesis"] == "exact\x00synthesis"
        assert "versions" not in result["themes"][0]
        assert len(statements) == 1 and not session.identity_map
        assert session.get(ResearchEntry, theme["theme_id"]).context_json == original


def test_legacy_inactive_card_does_not_guess_ownership_from_text_author(postgres_watchlist_env):
    iid = postgres_watchlist_env['instrument_id']
    expected = {}
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund',
                                    instrument_name='Legacy ownership', is_active=True, metadata_json={}))
        session.flush()
        for status in ('paused', 'closed'):
            for owner in ('user', 'researcher'):
                theme = save_theme(session, iid, ThemeInput(title=f'{status} {owner}'))
                entry = session.get(ResearchEntry, theme['theme_id'])
                context = {key: value for key, value in entry.context_json.items() if key != 'lifecycle_owner'}
                entry.context_json = {**context, 'theme_status': status,
                    'updated_by_role': 'researcher' if owner == 'user' else 'user',
                    'versions': [{'status': 'active', 'updated_by_role': 'researcher'},
                                 {'status': status, 'updated_by_role': owner, 'text': 'retained\x00original' * 2000}]}
                expected[entry.entry_id] = owner
        session.commit()
    with get_session_factory()() as session:
        statements = []
        event.listen(session, 'do_orm_execute', lambda state: statements.append(state.statement))
        summaries = theme_summaries(session, iid)['themes']
        assert len(summaries) == 4
        assert all(theme['lifecycle_owner'] is None and 'versions' not in theme for theme in summaries)
        assert len(statements) == 1 and not session.identity_map
        for theme in summaries:
            entry = session.get(ResearchEntry, theme['theme_id'])
            assert theme_record(entry)['lifecycle_owner'] == expected[entry.entry_id]
            assert 'lifecycle_owner' not in entry.context_json


def test_page_and_scheduler_reads_never_transfer_computed_source_trees(postgres_watchlist_env):
    from watchlist_app.services.research_dossier import _notebooks
    from watchlist_app.services.sector_research import review_states
    from watchlist_app.services.research_read_projection import browser_source_view
    from watchlist_app.services.research_triggers import trigger_dossier
    iid = postgres_watchlist_env["instrument_id"]
    original_text = "UNSELECTED_COMPUTED_ORIGINAL" * 10000 + "\x00"
    source = {"source_id": "computed:retained", "source_type": "computed_metric", "title": r"Literal\u0000 title",
        "as_of": "2026-10-01", "methodology": {"metric": "ewma_volatility"},
        "metadata": {"published_at": "2026-09-30", "unselected": original_text},
        "sources": [{"text": original_text}], "input_snapshot": {"original": original_text},
        "input_series": [{"date": "2026-09-30", "value": 3, "original": original_text}],
        "source_ids": [original_text], "data": {"analysis_kind": "watchlist_observations", "raw": original_text}}
    notebook = {"version_id": "saved-version", "investment_view": {"direction": "Current judgment",
        "updated_at": "2026-10-01", "versions": [{"direction": "Historical judgment"}]}, "sources": [source]}
    context = {"sector_run": True, "instrument_ids": [iid], "cutoff": "2026-10-01T00:00:00Z",
        "reviews": {iid: {"status": "completed", "research": notebook}}}
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Computed evidence", is_active=True, metadata_json={}))
        session.flush()
        theme = save_theme(session, iid, ThemeInput(title="Computed evidence theme"))
        entry = session.get(ResearchEntry, theme["theme_id"])
        entry.context_json = {**entry.context_json, "sources": [source], "versions": [{"sources": [source]}]}
        session.add(ResearchTopic(topic_id=f"instrument-events:{iid}", title="Research", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="source-projection-run", topic_id=f"instrument-events:{iid}", kind="analysis",
            title="Saved", status="completed", created_at=datetime.now(UTC), context_json=context))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "UNSELECTED_COMPUTED_ORIGINAL" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _connection, _cursor, statement, *_: statements.append(statement))
    try:
        with Session(engine) as session:
            shown = theme_summaries(session, iid)["themes"][0]
            assert shown["sources"] == [browser_source_view(source)]
            assert "versions" not in shown
            current, _ = _notebooks(session, iid, False, source_metadata_only=True)
            assert current["sources"] == [browser_source_view(source)]
            state = review_states(session, instrument_ids=[iid], summary_only=True)["latest"][iid]
            assert state["current_summary"] == "Current judgment"
            assert "versions" not in state["current_research"]["investment_view"]
            assert "sources" not in state["current_research"]
            trigger = trigger_dossier(session, iid)
            assert trigger["notebook"]["sources"] == [browser_source_view(source)]
            assert trigger["themes"][0]["sources"] == [browser_source_view(source)]
            assert not any("research_entry.context_json" in statement for statement in statements)
            assert not session.identity_map
    finally:
        engine.dispose()
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, theme["theme_id"]).context_json["sources"] == [source]
        assert session.get(ResearchEntry, "source-projection-run").context_json == context
