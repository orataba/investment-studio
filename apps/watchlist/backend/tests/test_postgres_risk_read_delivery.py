"""PostgreSQL row locks protect parallel delivery receipts on the same run."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest

from watchlist_app.api.routes import risk_officer as route
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_risk_read_delivery import bound_context

pytestmark = pytest.mark.postgresql_integration


def test_parallel_risk_pages_refresh_locked_row_and_merge_delivery(postgres_watchlist_env):
    factory = get_session_factory()
    with factory() as session:
        run, _ = service.begin_run(session, watchlist_id="delivery-scope")
        run.status = "running"
        run.context_json = {**run.context_json, **bound_context()}
        session.commit()
        run_id = run.entry_id
    loaded = Event()
    def second_page():
        with factory() as session:
            cached = session.get(ResearchEntry, run_id)  # Like the access middleware before the route lock.
            assert cached.context_json["risk_delivered_pages"] == []
            loaded.set()
            route.read_risk_page(run_id, route.RiskReadInput(instrument_id="risk-b", section="research_context"), session)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as first:
            route._running_risk_run(first, run_id)
            pending = pool.submit(second_page)
            assert loaded.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.2)
                route.read_risk_page(run_id, route.RiskReadInput(instrument_id="risk-a", section="research_context"), first)
            finally:
                first.rollback()
        pending.result(timeout=5)
    with factory() as session:
        assert sorted(session.get(ResearchEntry, run_id).context_json["risk_delivered_pages"]) == [
            ["risk-a", "research_context", 0], ["risk-b", "research_context", 0]]
