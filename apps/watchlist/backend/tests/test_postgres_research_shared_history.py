"""Exercise historical preparation through the production PostgreSQL projection."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from studio_identity import principal_context

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_run_context import load_run_fields, load_shared_history_fields
from watchlist_app.services.research_fmp_scope import latest_research_focus
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_research_fmp_scope import add_research, clock
from .test_research_shared_history import seed_current

pytestmark = pytest.mark.postgresql_integration


def test_delegated_shared_history_does_not_decode_originals_or_relax_run_scope(postgres_watchlist_env):
    with get_session_factory()() as session:
        add_research(session, "previous", ["fmp:AAA"])
        row = session.get(ResearchEntry, "previous")
        row.context_json = {**row.context_json, "input_snapshot_cutoff": clock(3).isoformat(),
                            "large_original": "DO_NOT_DECODE_HISTORY" * 10000 + "\x00"}
        actor = seed_current(session)
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "DO_NOT_DECODE_HISTORY" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
                           connect_args={"options": "-c search_path=watchlist,instrument_data,public"})
    try:
        with principal_context(actor), Session(engine) as session:
            assert load_shared_history_fields(session, "previous", "xlk", {"input_snapshot_cutoff"}).context_json == {
                "input_snapshot_cutoff": clock(3).isoformat()}
            assert latest_research_focus(session, "xlk", clock(8)) == ["AAA"]
            with pytest.raises(HTTPException) as error:
                load_run_fields(session, "previous", {"input_snapshot_cutoff"}, summary_only=True)
            assert error.value.status_code == 403
    finally:
        engine.dispose()
