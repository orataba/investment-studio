"""Read-only PostgreSQL literals: no application tables, schema or records written."""
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, String, cast, create_engine, event, literal, select, true
from sqlalchemy.orm import Session

from watchlist_app.services import research_access as access


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
