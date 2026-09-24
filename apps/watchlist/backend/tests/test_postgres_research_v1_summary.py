"""V1 card projection preserves PostgreSQL JSON provenance and team scope."""
from copy import deepcopy

import pytest
from sqlalchemy import event

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_themes import ThemeInput, save_theme, theme_summaries
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
