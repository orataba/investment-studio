"""Read-only PostgreSQL literals: no application tables, schema or records written."""
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import DateTime, JSON, String, cast, create_engine, event, literal, select, true
from sqlalchemy.orm import Session

from watchlist_app.services import research_access as access


@pytest.mark.postgresql_integration
def test_streamed_projection_closes_server_cursor_before_decoding_old_originals(monkeypatch):
    url = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured")
    original = {"selected": "exact\x00original", "literal": r"\u0000"}
    old = {"selected": "unneeded-history-must-not-decode", "retained_source": "old source " * 80000}
    table = select(literal(1).label("rank"), cast(literal(json.dumps(original)), JSON).label("context_json"))
    table = table.union_all(select(literal(2), cast(literal(json.dumps(old)), JSON))).cte("retained_fixture")
    monkeypatch.setattr(access, "ResearchEntry", SimpleNamespace(context_json=table.c.context_json))
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "unneeded-history-must-not-decode" not in value
        return json.loads(value)
    engine = create_engine(url, json_deserializer=deserialize,
        connect_args={"options": "-c default_transaction_read_only=on"})
    cursors = []
    event.listen(engine, "after_cursor_execute", lambda _conn, cursor, *_: cursors.append(cursor))
    try:
        with Session(engine) as session:
            relation, values = access.research_context_projection(session, {"selected": String})
            query = select(values["selected"].label("selected")).select_from(table).join(relation, true()).order_by(table.c.rank)
            rows = access.iter_research_projection_rows(session, query, {"selected": ("selected",)})
            assert next(rows).selected == original["selected"]
            rows.close()
            assert cursors[-1].name and cursors[-1].closed
    finally:
        engine.dispose()


@pytest.mark.postgresql_integration
def test_projection_preserves_nul_originals_and_literal_escapes_with_one_select(monkeypatch):
    url = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured")
    original = {"portfolio_id": "old-portfolio", "risk_scope": {"portfolio_id": "risk-portfolio"},
        "research_actor": {"user_id": "pm-one"}, "instrument_ids": ["xlk"],
        "reviews": {"xlk": {"status": "completed", "research": {"sources": [{
            "source_id": "original", "text": "before\x00\x00after\\\x00", "body_sha256": "unchanged-hash"}]}}},
        "literal": r"\u0000", "unselected": "旧完整上下文" * 40000}
    literal_only = {"portfolio_id": r"literal-\u0000", "literal": r"\u0000", "unselected": "健康大包" * 40000}
    fixtures = {"nul": original, "literal": literal_only, "healthy": {"portfolio_id": "healthy"}}
    table = select(literal("nul").label("entry_id"), cast(literal(json.dumps(original)), JSON).label("context_json"))
    table = table.union_all(*(select(literal(key), cast(literal(json.dumps(value)), JSON))
                              for key, value in fixtures.items() if key != "nul")).cte("retained_fixture")
    monkeypatch.setattr(access, "ResearchEntry", SimpleNamespace(context_json=table.c.context_json))
    engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    calls = []
    event.listen(engine, "before_cursor_execute", lambda *_: calls.append(True))
    try:
        with Session(engine) as session:
            relation, values = access.research_context_projection(session, {
                "portfolio_id": String, "risk_scope": JSON, "research_actor": JSON, "reviews": JSON, "literal": String})
            query = select(table.c.entry_id, values["portfolio_id"].label("portfolio_id"),
                values["risk_scope"]["portfolio_id"].as_string().label("risk_portfolio_id"),
                values["research_actor"].label("research_actor"), values["literal"].label("literal"),
                values["reviews"]["xlk"]["research"].label("research")).select_from(table).join(relation, true())
            rows = access.research_projection_rows(session, query, {
                "portfolio_id": ("portfolio_id",), "risk_portfolio_id": ("risk_scope", "portfolio_id"),
                "research_actor": ("research_actor",), "literal": ("literal",), "research": ("reviews", "xlk", "research")})
            assert len(calls) == 1
            result = {row.entry_id: row for row in rows}
            assert result["nul"].research == original["reviews"]["xlk"]["research"]
            assert result["nul"].research_actor == original["research_actor"]
            assert result["nul"].portfolio_id == "old-portfolio" and result["nul"].risk_portfolio_id == "risk-portfolio"
            assert result["nul"].literal == result["literal"].literal == r"\u0000"
            assert result["literal"].portfolio_id == literal_only["portfolio_id"]
            assert all("unselected" not in row._mapping and "_original_context" not in row._mapping for row in rows)
            affected, _ = access._postgres_projection_context()
            raw_rows = session.execute(select(table.c.entry_id, affected.label("affected"))).all()
            assert dict(raw_rows) == {"nul": True, "literal": False, "healthy": False}
    finally:
        engine.dispose()


@pytest.mark.postgresql_integration
def test_review_receipts_preserve_nul_text_clocks_and_publication_scope(monkeypatch):
    from watchlist_app.db.models import workbench
    from watchlist_app.services import research_activity as activity

    url = os.environ.get("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured")
    cutoff = "2026-09-03T00:00:00+00:00"
    reflection = {"status": "reviewed", "summary": "retained\x00receipt; literal " + r"\u0000",
                  "reviewed_update_ids": ["research:original"]}
    context = {"cutoff": cutoff, "reviews": {"xlk": {"status": "limited", "reflection": reflection}},
               "unselected_source": "retained\x00source"}
    fixtures = [
        ("published", "team-a", "completed", None, context),
        ("private-history", "team-a", "completed", datetime(2026, 9, 4, tzinfo=UTC), context),
        ("other-team", "team-b", "completed", datetime(2026, 9, 5, tzinfo=UTC), context),
        ("failed", "team-a", "failed", datetime(2026, 9, 6, tzinfo=UTC), context),
        ("rejected-review", "team-a", "completed", datetime(2026, 9, 7, tzinfo=UTC),
         {**context, "reviews": {"xlk": {"status": "failed", "reflection": reflection}}}),
        ("literal-only", "team-a", "completed", datetime(2026, 9, 2, tzinfo=UTC),
         {"reviews": {"xlk": {"status": "completed", "reflection": {"status": "reviewed",
             "summary": r"literal \u0000", "reviewed_update_ids": ["research:literal"]}}}}),
    ]
    selects = [select(literal(topic).label("topic_id"), literal(team).label("team_id"),
        literal("analysis").label("kind"), literal(status).label("status"),
        literal(completed, type_=DateTime(timezone=True)).label("completed_at"),
        literal(datetime(2026, 9, 3, tzinfo=UTC)).label("created_at"),
        cast(literal(json.dumps(payload)), JSON).label("context_json"))
        for topic, team, status, completed, payload in fixtures]
    table = selects[0].union_all(*selects[1:]).cte("receipt_fixture")
    for column in table.c:
        setattr(table, column.name, column)
    monkeypatch.setattr(workbench, "ResearchEntry", table)
    monkeypatch.setattr(access, "ResearchEntry", table)
    monkeypatch.setattr(activity, "current_principal", lambda: SimpleNamespace(local_unrestricted=False, team_id="team-a"))
    monkeypatch.setattr(access, "topic_portfolio_ids", lambda _session, topic:
        {"retained-portfolio"} if topic.topic_id == "private-history" else set())
    engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    try:
        with Session(engine) as session:
            monkeypatch.setattr(session, "get", lambda _model, topic_id: SimpleNamespace(topic_id=topic_id, team_id="team-a"))
            receipts = activity.review_receipts(session, "xlk")
            assert receipts == {
                "research:original": {"last_reviewed_at": cutoff, "last_review_status": "reviewed",
                                      "last_review_summary": reflection["summary"]},
                "research:literal": {"last_reviewed_at": "2026-09-02T00:00:00+00:00",
                                     "last_review_status": "reviewed", "last_review_summary": r"literal \u0000"},
            }
            assert session.execute(select(table.c.context_json).where(table.c.topic_id == "published")).scalar_one() == context
    finally:
        engine.dispose()
