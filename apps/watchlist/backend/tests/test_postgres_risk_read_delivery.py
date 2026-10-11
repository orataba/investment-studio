"""PostgreSQL row locks protect parallel delivery receipts on the same run."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from threading import Event

import pytest
from fastapi import HTTPException
from studio_identity import current_principal, principal_context

from watchlist_app.api.routes import risk_officer as route
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_risk_read_delivery import bound_context

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("new_attempt_while_waiting", [False, True])
def test_parallel_risk_pages_refresh_locked_row_and_merge_delivery(postgres_watchlist_env, new_attempt_while_waiting):
    factory = get_session_factory()
    with factory() as session:
        run, _ = service.begin_run(session, watchlist_id="delivery-scope")
        run.status = "running"
        run.context_json = {**run.context_json, **bound_context(), "execution": {"attempt": 1}}
        session.commit()
        run_id = run.entry_id
    principal = replace(current_principal(), resource_scope={"kind": "run", "id": run_id, "attempt": "1"})
    loaded = Event()
    def second_page():
        with principal_context(principal), factory() as session:
            cached = session.get(ResearchEntry, run_id)  # Like the access middleware before the route lock.
            assert cached.context_json["risk_delivered_pages"] == []
            loaded.set()
            route.read_risk_page(run_id, route.RiskReadInput(instrument_id="risk-b", section="research_context"), session)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with principal_context(principal), factory() as first:
            locked = route._running_risk_run(first, run_id)
            pending = pool.submit(second_page)
            assert loaded.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.2)
                if new_attempt_while_waiting:
                    record = first.get(ResearchEntry, run_id)
                    record.context_json = {**record.context_json, "execution": {"attempt": 2}}
                    first.commit()
                else:
                    route.read_risk_page(run_id, route.RiskReadInput(instrument_id="risk-a", section="research_context"), first)
            finally:
                first.rollback()
        if new_attempt_while_waiting:
            with pytest.raises(HTTPException) as rejected:
                pending.result(timeout=5)
            assert rejected.value.status_code == 409
        else:
            pending.result(timeout=5)
    with factory() as session:
        from watchlist_app.db.models.workbench import ResearchRiskReadState
        row = session.get(ResearchRiskReadState, run_id)
        assert sorted(row.delivered_pages if row else []) == (
            [] if new_attempt_while_waiting else [["risk-a", "research_context", 0], ["risk-b", "research_context", 0]])
