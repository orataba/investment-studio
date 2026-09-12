from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from studio_identity import IdentityError, Principal

from briefing_app.db import Base, Report, get_session
from briefing_app.main import create_app
from briefing_app.settings import Settings


@pytest.fixture
def fixture(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        for index, (key, state, edition) in enumerate([("public", "completed", "publisher"), ("preview", "completed", "preview"), ("draft", "running", "publisher")]):
            session.add(Report(report_id=key, team_id="default", report_type="daily", report_date="2026-09-07", version=index,
                cutoff=datetime.now(UTC), status=state, input_json={"edition_role": edition,
                "sources": [{"source_id": "cited", "title": "published title"}, {"source_id": "internal", "title": "unpublished input"}]},
                result_json={"source_ids": ["cited"]} if state == "completed" else None))
        session.commit()
    app = create_app(Settings(database_url="sqlite://", cors_origins=["http://127.0.0.1:5175"]))
    def sessions():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    actors = {
        "member": Principal("a", "甲", "default", credential="member"),
        "reader": Principal("b", "乙", "default", team_role="reader", credential="reader"),
        "foreign": Principal("c", "丙", "other", credential="foreign"),
        "task": Principal(None, "简报任务", "default", kind="service", service_id="svc", scopes=["briefing:publish"], credential="task", resource_scope={"kind": "report", "id": "draft"}),
        "wrong-service": Principal(None, "维护任务", "default", kind="service", service_id="other", scopes=["portfolio:maintain"], credential="wrong-service"),
    }
    def resolve(request, *args, **kwargs):
        from studio_identity import credential_from_headers, validate_origin
        token, cookie = credential_from_headers(request.headers)
        validate_origin(request, cookie_authenticated=cookie)
        if token not in actors:
            raise IdentityError(401, "已撤销")
        return actors[token]
    monkeypatch.setattr("briefing_app.main.resolve_request", resolve)
    yield TestClient(app), factory, actors
    engine.dispose()


def test_anonymous_sees_only_published_projection(fixture):
    client, _, _ = fixture
    result = client.get("/api/briefing/reports").json()
    assert [r["report_id"] for r in result["rows"]] == ["public"] and result["total"] == 1
    body = client.get("/api/briefing/reports/public").json()
    assert [s["source_id"] for s in body["sources"]] == ["cited"]
    assert "unpublished input" not in str(body)
    for suffix in ["preview", "draft", "draft/status"]:
        assert client.get("/api/briefing/reports/" + suffix).status_code == 404
    for suffix in ["public/context", "public/source-index", "public/sources/cited"]:
        assert client.get("/api/briefing/reports/" + suffix).status_code == 401
    assert client.post("/api/briefing/reports", json={}).status_code == 401
    assert client.get("/api/briefing/status").json()["can_generate"] is False


def test_member_and_report_scoped_service_cannot_escape_scope(fixture):
    client, _, actors = fixture
    def headers(token):
        return {"Authorization": "Bearer " + token}
    assert client.get("/api/briefing/reports/preview/context", headers=headers("member")).status_code == 200
    assert client.get("/api/briefing/reports/preview/context", headers=headers("foreign")).status_code == 404
    assert client.get("/api/briefing/reports/draft/context", headers=headers("task")).status_code == 200
    assert client.get("/api/briefing/reports/public/context", headers=headers("task")).status_code == 404
    assert client.get("/api/briefing/reports", headers=headers("wrong-service")).status_code == 403
    assert client.post("/api/briefing/reports", headers=headers("reader"), json={"report_type": "daily", "cutoff": "2026-09-07T00:00:00Z"}).status_code == 403
    actors.pop("task")
    assert client.get("/api/briefing/reports/draft/context", headers=headers("task")).status_code == 401


def test_login_failure_is_readable_from_trusted_frontend(fixture):
    client, _, _ = fixture
    response = client.get("/api/briefing/reports/public/context", headers={"Origin": "http://127.0.0.1:5175"})
    assert response.status_code == 401
    assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5175"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_local_owner_gets_management_capabilities_without_login(fixture, monkeypatch):
    client, _, _ = fixture
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "local")
    monkeypatch.setattr("briefing_app.main.resolve_request", lambda *a, **kw:
        Principal("owner", "shaw", "default", team_role="admin", is_team_owner=True, local_unrestricted=True))
    response = client.get("/api/briefing/status")
    assert response.status_code == 200
    assert response.json()["can_generate"] is True
    assert response.json()["can_read_sources"] is True


def test_revoked_actor_cannot_publish_completed_model_output(fixture, monkeypatch):
    client, factory, _ = fixture
    from briefing_app import runner
    with factory() as session:
        report = session.get(Report, "draft")
        report.status = "queued"
        session.commit()
    monkeypatch.setattr(runner, "get_session_factory", lambda: factory)
    monkeypatch.setattr(runner, "build_input", lambda *a: {"source_count": 1, "edition_role": "publisher"})
    valid = Principal("a", "甲", "default", team_role="admin", resource_scope={"kind": "report", "id": "draft"})
    calls = 0
    def resolve(*args):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise IdentityError(401, "已撤销")
        return valid
    monkeypatch.setattr(runner, "resolve_token", resolve)
    monkeypatch.setattr(runner, "_run_harness", lambda *a: {"sections": []})
    monkeypatch.setattr(runner, "revoke_delegation", lambda *a: None)
    monkeypatch.setattr(runner, "issue_delegation", lambda *a: "stage-token")
    runner.run_report("draft", "task-token", Principal("a", "甲", "default", team_role="admin", credential="issuer"))
    with factory() as session:
        report = session.get(Report, "draft")
        assert report.status == "failed" and report.result_json is None
