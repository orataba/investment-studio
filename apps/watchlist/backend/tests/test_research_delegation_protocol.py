"""The durable worker's actual HTTP grants must obey Home's revoke contract."""
from datetime import timedelta
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import studio_identity
from studio_identity import issue_delegation, resolve_token, revoke_delegation, principal_context

from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_execution as queue, research_runner as runner

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "home/backend"))
from home_api.api.routes import auth
from home_api.cli import bootstrap
from home_api.core.settings import Settings
from home_api.db.models import Delegation, ServiceCredential, now
from home_api.db.session import database_engine, get_db, initialize_schema
from home_api.main import app as home_app
from home_api.services import identity as home_identity
from home_api.services.auth import hash_password, token_hash


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("stage_seconds", [7200, 10800])
def test_worker_revokes_stage_children_without_parent_authority(client, monkeypatch, tmp_path, interrupted, stage_seconds):
    monkeypatch.setattr(runner.get_settings(), "research_stage_timeout_seconds", stage_seconds)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'identity.db'}")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(home_identity, "get_settings", lambda: settings)
    initialize_schema(settings.database_url)
    engine = database_engine(settings.database_url)
    with Session(engine) as session:
        bootstrap(session, username="owner", display_name="Owner", team_name="Team",
                  password_hash=hash_password("test-only-long-password"))
        session.add(ServiceCredential(service_id="research", display_name="Research", team_id="default",
            scopes=["identity:delegate", "watchlist:research"], audiences=["identity", "watchlist"],
            token_hash=token_hash("test-only-service"), expires_at=now() + timedelta(days=2)))
        session.commit()

    def sessions():
        with Session(engine) as session:
            yield session

    home_app.dependency_overrides[get_db] = sessions
    identity_client = TestClient(home_app, base_url="https://testserver")
    responses = []

    def transport(method, url, *, headers, body, **kwargs):
        response = identity_client.request(method, urlsplit(url).path, headers=headers, content=body)
        responses.append((urlsplit(url).path, response.status_code))
        return SimpleNamespace(status=response.status_code, data=response.content)

    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_URL", "https://testserver/api/auth")
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN", "test-only-service")
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "account")
    monkeypatch.delenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.setattr(studio_identity._http, "request", transport)
    monkeypatch.setattr(runner, "issue_delegation", issue_delegation)
    for module in (runner, queue):
        monkeypatch.setattr(module, "resolve_token", resolve_token)
        monkeypatch.setattr(module, "revoke_delegation", revoke_delegation)

    def execute(run_id, *, execution_authorization, **kwargs):
        execution_authorization()
        if interrupted:
            raise runner.ResearchExecutionInterrupted()
        with get_session_factory()() as session:
            session.get(ResearchEntry, run_id).status = "completed"
            session.commit()

    monkeypatch.setattr(runner, "_run_analysis", execute)
    try:
        actor = resolve_token("test-only-service", "watchlist")
        with principal_context(actor), get_session_factory()() as session:
            session.add(ResearchTopic(topic_id="grant-chain", title="Research", team_id=actor.team_id, visibility="team"))
            session.flush()
            session.add(ResearchEntry(entry_id="grant-chain", topic_id="grant-chain", kind="analysis",
                title="Research", status="queued", context_json={"research_actor": actor.to_dict()}))
            session.commit()
            queue.enqueue_run(actor, "grant-chain")
        claim = queue.claim_next("worker")
        queue.execute_claim(claim, Event())
        with Session(engine) as session:
            grants = list(session.scalars(select(Delegation)))
            parent, = [grant for grant in grants if grant.parent_kind == "service"]
            children = [grant for grant in grants if grant.parent_kind == "delegation"]
            assert len(children) == 2
            assert all(grant.parent_id == parent.id and grant.revoked_at is not None for grant in children)
            assert all(stage_seconds + 299 <= (grant.expires_at - grant.created_at).total_seconds()
                       <= stage_seconds + 300 for grant in children)
            assert 24 * 3600 - 1 <= (parent.expires_at - parent.created_at).total_seconds() <= 24 * 3600
            assert (parent.revoked_at is None) is interrupted
        assert all(status < 400 for _, status in responses)
        assert sum(path.endswith("/revoke") for path, _ in responses) == (2 if interrupted else 3)
        with get_session_factory()() as session:
            job = session.get(ResearchExecution, "grant-chain")
            assert job.status == ("running" if interrupted else "finished")
            assert (job.delegation_token is not None) is interrupted
    finally:
        home_app.dependency_overrides.pop(get_db, None)
        identity_client.close()
        engine.dispose()
