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


def test_selected_run_reads_and_authorization_skip_large_unrelated_json(postgres_watchlist_env):
    from types import SimpleNamespace
    from studio_identity import Principal, principal_context
    from watchlist_app.api.routes.workbench import read_run_page, ResearchReadInput, run_computed_source, run_dossier
    from watchlist_app.services.research_access import enforce_request
    context = {"research_run": True, "run_id": "projected-runtime", "cutoff": "2026-09-24T00:00:00Z",
        "instrument_ids": ["xlk"], "question": r"完整问题含字面\u0000",
        "research_actor": {"user_id": "pm-one", "kind": "user"}, "execution": {"attempt": 1},
        "research_dossiers": [{"instrument_id": "xlk", "notebook": None}],
        "computed_metrics": [{"source_id": "metric", "source_type": "computed_metric", "data": {"value": 3}}],
        "unrelated_original": "DO_NOT_HYDRATE_FULL_RUN" * 100000 + "\x00"}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="runtime-projection", title="Projection", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="projected-runtime", topic_id="runtime-projection", kind="analysis",
            title="Projection", status="running", context_json=context))
        session.commit()
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "DO_NOT_HYDRATE_FULL_RUN" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    principal = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "projected-runtime", "attempt": "1"})
    try:
        with principal_context(principal), Session(engine) as session:
            enforce_request(SimpleNamespace(method="POST", url=SimpleNamespace(
                path="/api/research/runs/projected-runtime/numeric")), session)
            page = read_run_page("projected-runtime", ResearchReadInput(resource="context", section="question"), session)
            assert page["data"] == context["question"]
            assert run_computed_source("projected-runtime", "metric", session) == {**context["computed_metrics"][0],
                "retained_reference": {"run_id": "projected-runtime", "path": ["computed_metrics", "*"]}}
            assert run_dossier("projected-runtime", "xlk", session=session)["instrument_id"] == "xlk"
    finally:
        engine.dispose()


def test_private_history_projects_latest_active_run_without_loading_source_bodies(postgres_watchlist_env):
    from datetime import UTC, datetime
    from watchlist_app.api.routes.workbench import topics

    stamp = datetime(2026, 10, 1, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id='reopen-private', title='Reopen', visibility='private', created_by_user_id='reopen-owner'))
        session.flush()
        for run_id in ('a-older-tie', 'z-newer-tie'):
            session.add(ResearchEntry(entry_id=run_id, topic_id='reopen-private', kind='analysis',
                title=run_id, status='running', created_at=stamp, context_json={
                    'watchlist_id': 'test-scope', 'private_source': 'never return this body\x00' * 1000}))
        session.commit()
    from studio_identity import Principal, principal_context
    with principal_context(Principal('reopen-owner', 'Owner', 'default')), get_session_factory()() as session:
        rows = topics(session=session)
        selected = next(row for row in rows if row['topic_id'] == 'reopen-private')
        assert selected['active_run']['entry_id'] == 'z-newer-tie'
        assert selected['active_run']['watchlist_id'] == 'test-scope'
        assert 'private_source' not in str(rows)
        assert not any(isinstance(obj, ResearchEntry) for obj in session.identity_map.values())


def test_topic_detail_polls_only_display_fields_and_exact_receipt_index(postgres_watchlist_env):
    from studio_identity import Principal, principal_context
    from watchlist_app.api.routes.workbench import topic_detail, run_evidence

    original = {"text": "Exact original\x00with literal \\u0000", "points": [1, 2, 3]}
    receipt = {"source_id": "source:old", "tool": "source", "request": {"note": "receipt\x00note"},
               "retrieved_at": "2026-10-01", "result": original}
    context = {"cutoff": "2026-10-01", "instrument_ids": [], "execution": {"attempt": 1},
        "page_context": {"tab": r"literal\u0000"}, "tool_evidence": [receipt],
        "unrelated_archive": "DO_NOT_FETCH_WHOLE_CONTEXT" * 100000 + "\x00"}
    material = {"file_name": "attachment.txt", "extraction": "exact\x00metadata"}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="polling-topic", title="Polling", visibility="private", created_by_user_id="poll-owner"))
        session.flush()
        session.add_all([
            ResearchEntry(entry_id="polling-run", topic_id="polling-topic", kind="analysis", title="Question",
                status="draft", body="Complete answer " * 1000, context_json=context),
            ResearchEntry(entry_id="polling-file", topic_id="polling-topic", kind="evidence", title="Attachment", context_json=material),
        ])
        session.commit()

    polling = True
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        if polling:
            assert "DO_NOT_FETCH_WHOLE_CONTEXT" not in value
        return json.loads(value)

    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with principal_context(Principal("poll-owner", "Owner", "default")), Session(engine) as session:
            for _ in range(2):
                page = topic_detail("polling-topic", session)
                entries = {item["entry_id"]: item for item in page["entries"]}
                run = entries["polling-run"]
                assert run["body"] == "Complete answer " * 1000
                assert run["context_json"]["page_context"] == context["page_context"]
                assert run["context_json"]["tool_evidence"] == [{key: receipt.get(key) for key in ("source_id", "tool", "request", "retrieved_at")}]
                assert entries["polling-file"]["context_json"] == material
                assert "unrelated_archive" not in run["context_json"]
                assert not any(isinstance(obj, ResearchEntry) for obj in session.identity_map.values())
            # Explicit original reads retain the existing exceptional NUL
            # recovery path; that path must never run during status polling.
            polling = False
            assert run_evidence("polling-run", "source:old", session) == receipt
    finally:
        engine.dispose()
