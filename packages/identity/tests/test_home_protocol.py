"""Exercise the shared HTTP client against the actual Home account API."""
from datetime import timedelta
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import studio_identity as identity

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "home/backend"))
from home_api.api.routes import auth
from home_api.cli import bootstrap
from home_api.core.settings import Settings
from home_api.db.models import ServiceCredential, now
from home_api.db.session import database_engine, get_db, initialize_schema
from home_api.main import app
from home_api.services.auth import hash_password, token_hash


def test_real_protocol_regrant_keeps_actor_and_parent_revocation(monkeypatch, tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'identity.db'}", frontend_url="https://testserver", cors_origins=["https://testserver"])
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    initialize_schema(settings.database_url)
    engine = database_engine(settings.database_url)
    with Session(engine) as session:
        owner = bootstrap(session, username="pm", display_name="经理", team_name="团队", password_hash=hash_password("long-test-password"))
        session.add(ServiceCredential(service_id="bridge", display_name="应用间委托", team_id="default", scopes=["identity:delegate"],
            audiences=["identity"], token_hash=token_hash("backend-private"), expires_at=now() + timedelta(days=1)))
        session.commit()
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = sessions
    client = TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_URL", "https://testserver/api/auth")
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN", "backend-private")
    monkeypatch.delenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE", raising=False)
    def transport(method, url, *, headers, body, **kwargs):
        response = client.request(method, urlsplit(url).path, headers=headers, content=body)
        return SimpleNamespace(status=response.status_code, data=response.content)
    monkeypatch.setattr(identity._http, "request", transport)
    try:
        assert client.post("/api/auth/login", json={"username": "pm", "password": "long-test-password"}).status_code == 200
        principal = identity.resolve_token(client.cookies.get(settings.auth_cookie_name), "watchlist")
        assert principal.user_id == owner["user_id"]
        assert identity.team_directory(principal)[0]["user_id"] == owner["user_id"]
        token = identity.issue_delegation(principal, "watchlist", {"kind": "run", "id": "run-one"})
        run = identity.resolve_token(token, "watchlist")
        child = identity.issue_delegation(run, "portfolio", {"kind": "portfolio", "id": "p1"})
        assert identity.resolve_token(child, "portfolio").user_id == principal.user_id
        with pytest.raises(identity.IdentityError) as caught:
            identity.resolve_token(child, "watchlist")
        assert caught.value.status_code == 403
        # Home refuses a model's unaided regrant, despite an otherwise valid parent task token.
        forbidden = client.post("/api/auth/delegations", headers={"Authorization": "Bearer " + token}, json={"audience": "portfolio", "resource_scope": {"kind": "portfolio", "id": "p2"}})
        assert forbidden.status_code == 403
        identity.revoke_delegation(token)
        with pytest.raises(identity.IdentityError) as caught:
            identity.resolve_token(child, "portfolio")
        assert caught.value.status_code == 401
        client.post("/api/auth/logout-all")
        with pytest.raises(identity.IdentityError):
            identity.resolve_token(principal.credential, "watchlist")
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
