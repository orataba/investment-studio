"""A NUL in an unrelated retained source must not expand a small projection."""
import json

import pytest
from sqlalchemy import JSON, Text, create_engine, select, true
from sqlalchemy.orm import Session

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_access import research_context_projection, research_projection_rows

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env


pytestmark = pytest.mark.postgresql_integration


def test_only_affected_selected_fields_restore_original_context(postgres_watchlist_env):
    contexts = {
        "unrelated-nul": {"role": "small scope", "selected": {"nested": "exact"},
                          "source": "retained source " * 10000 + "\x00"},
        "selected-scalar-nul": {"role": "exact\x00role", "selected": {"nested": "exact"}},
        "selected-json-nul": {"role": "exact", "selected": {"nested": ["exact\x00value"]}},
        "existing-replacement": {"role": "existing \ufffd", "selected": {"nested": "exact"},
                                 "source": "unrelated\x00source"},
        "replacement-without-nul": {"role": "existing \ufffd", "selected": {"nested": "existing \ufffd"}},
        "literal-escape": {"role": r"literal\u0000", "selected": {"nested": r"literal\u0000"}},
        "literal-escape-unrelated-nul": {"role": r"literal\ufffd", "selected": {"nested": r"literal\u0000"},
                                        "source": "unrelated\x00source"},
        "empty-selected-unrelated-nul": {"role": None, "selected": None, "source": "unrelated\x00source"},
    }
    contexts = {key: {**value, "test_marker": key} for key, value in contexts.items()}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="field-projection", title="Projection", visibility="team"))
        session.flush()
        for key, context in contexts.items():
            session.add(ResearchEntry(entry_id=key, topic_id="field-projection", kind="analysis",
                                      title=key, context_json=context))
        session.commit()

    restored_originals = []
    expected_originals = {"selected-scalar-nul", "selected-json-nul", "existing-replacement"}

    def deserialize(value):
        result = json.loads(value)
        if isinstance(result, dict) and "test_marker" in result:
            assert result["test_marker"] in expected_originals, "An unrelated source was unnecessarily transferred"
            restored_originals.append(result["test_marker"])
        return result

    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with Session(engine) as session:
            relation, values = research_context_projection(session, {"role": Text, "selected": JSON})
            query = select(ResearchEntry.entry_id, values["role"].label("role"),
                           values["selected"].label("selected"), values["selected"]["nested"].label("nested"))
            query = query.select_from(ResearchEntry).join(relation, true()).where(
                ResearchEntry.topic_id == "field-projection").order_by(ResearchEntry.entry_id)
            rows = research_projection_rows(session, query,
                {"role": ("role",), "selected": ("selected",), "nested": ("selected", "nested")})
            assert len(rows) == len(contexts)
            for row in rows:
                original = contexts[row.entry_id]
                assert row.role == original["role"]
                assert row.selected == original["selected"]
                assert row.nested == (original["selected"] or {}).get("nested")
            assert set(restored_originals) == expected_originals
            assert not session.identity_map
    finally:
        engine.dispose()

    with get_session_factory()() as session:
        actual = {entry.entry_id: entry.context_json for entry in session.scalars(
            select(ResearchEntry).where(ResearchEntry.topic_id == "field-projection"))}
        assert actual == contexts
