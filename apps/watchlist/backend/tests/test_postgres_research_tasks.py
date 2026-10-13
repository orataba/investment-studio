"""Concurrent native children retain separate tasks under the run's row lock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from dataclasses import replace
from threading import Barrier

import pytest
from studio_identity import current_principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_tasks import ResearchTaskInput, create_task
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_parallel_native_tasks_merge_into_locked_run_without_lost_evidence(postgres_watchlist_env):
    actor = current_principal()
    iid = postgres_watchlist_env["instrument_id"]
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="native-tasks", title="Native tasks", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="native-tasks", topic_id="native-tasks", kind="analysis", title="Tasks",
            status="running", context_json={"sector_run": True, "research_actor": actor.to_dict(),
                "instrument_ids": [iid], "input_snapshot_cutoff": datetime.now(UTC).isoformat(),
                "execution": {"attempt": 1, "research_section": "overview"},
                "web_evidence": [{"text": "Preserved original\x00including NUL"}]}))
        session.commit()
    barrier = Barrier(2)
    principal = replace(actor, resource_scope={"kind": "run", "id": "native-tasks", "attempt": "1"})
    def child(role):
        with principal_context(principal), get_session_factory()() as session:
            stale = session.get(ResearchEntry, "native-tasks")
            barrier.wait(timeout=10)
            return create_task(session, stale, ResearchTaskInput(role=role, instrument_id=iid, instruction=f"Independent {role}"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(child, ["themes", "quantitative"]))
    with get_session_factory()() as session:
        context = session.get(ResearchEntry, "native-tasks").context_json
        assert set(context["research_tasks"]) == {row["task_id"] for row in results}
        assert {row["role"] for row in context["research_tasks"].values()} == {"themes", "quantitative"}
        assert context["web_evidence"][0]["text"] == "Preserved original\x00including NUL"
