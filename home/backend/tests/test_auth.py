from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_api.cli import bootstrap
from home_api.core.settings import Settings
from home_api.db.models import Delegation, Membership, ServiceCredential, SessionRecord, User, now
from home_api.db.session import database_engine, get_db, initialize_schema
from home_api.main import app
from home_api.services.auth import hash_password, token_hash

PASSWORD = "test-password-long"


@pytest.fixture()
def identity(monkeypatch, tmp_path: Path):
    from home_api.api.routes import auth
    from home_api.services import identity as identity_service
    key = tmp_path / "totp-key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'identity.db'}", frontend_url="https://testserver",
                        cors_origins=["https://testserver"], auth_totp_key_file=key)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(identity_service, "get_settings", lambda: settings)
    initialize_schema(settings.database_url)
    engine = database_engine(settings.database_url)
    with Session(engine) as db:
        owner = bootstrap(db, username="owner", display_name="经理甲", team_name="研究团队", password_hash=hash_password(PASSWORD))
        user = User(username="member", display_name="经理乙", password_hash=hash_password(PASSWORD))
        db.add(user); db.flush()
        db.add(Membership(user_id=user.id, team_id="default", role="member"))
        reader = User(username="reader", display_name="只读成员", password_hash=hash_password(PASSWORD))
        db.add(reader); db.flush()
        db.add(Membership(user_id=reader.id, team_id="default", role="reader"))
        db.commit()
        identifiers = {"owner": owner["user_id"], "member": user.id, "reader": reader.id}
    def override_db():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    yield settings, engine, identifiers
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def client_as(username="owner"):
    client = TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def credential(client) -> str:
    return client.cookies.get("__Secure-yungu_session")


def introspect(client, token, audience="watchlist"):
    return client.post("/api/auth/introspect", json={"audience": audience}, headers={"Authorization": f"Bearer {token}"})


def test_login_stable_identity_opaque_session_and_revoked_logout(identity):
    settings, engine, ids = identity
    client = client_as()
    token = credential(client)
    principal = client.get("/api/auth/session").json()
    assert principal["user_id"] == ids["owner"]
    assert principal["team_role"] == "admin"
    assert principal["is_team_owner"]
    assert client.get("/api/auth/check").status_code == 204
    with Session(engine) as db:
        stored = db.scalar(select(SessionRecord))
        assert stored.token_hash == token_hash(token)
        assert stored.token_hash != token
    assert introspect(client, token).json()["user_id"] == ids["owner"]
    assert client.post("/api/auth/logout").status_code == 204
    assert introspect(client, token).status_code == 401
    assert client.get("/api/auth/check").status_code == 401


def test_browser_origin_and_forged_identity_are_rejected(identity):
    client = client_as()
    assert client.patch("/api/auth/profile", json={"display_name": "伪造"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/auth/session", headers={"X-User-ID": "owner", "X-Team-Role": "admin"}).status_code == 401
    assert anonymous.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 403


def test_invite_activation_single_use_and_admin_boundaries(identity):
    owner = client_as()
    member = client_as("member")
    payload = {"username": "new-manager", "display_name": "新经理", "role": "member"}
    assert member.post("/api/auth/members", json=payload).status_code == 403
    result = owner.post("/api/auth/members", json=payload)
    assert result.status_code == 201
    token = urlsplit(result.json()["activation_url"]).fragment.removeprefix("token=")
    activation = {"token": token, "password": PASSWORD}
    assert owner.post("/api/auth/activate", json=activation).status_code == 204
    assert owner.post("/api/auth/activate", json=activation).status_code == 400
    newcomer = client_as("new-manager")
    assert newcomer.get("/api/auth/session").json()["team_role"] == "member"
    directory = newcomer.get("/api/auth/members").json()["members"]
    assert len(directory) == 4
    assert all("password_hash" not in record and "totp_secret" not in record for record in directory)


def test_disable_restore_reset_revoke_all_sessions_and_grants(identity):
    _, _, ids = identity
    owner = client_as()
    member = client_as("member")
    token = credential(member)
    grant = member.post("/api/auth/delegations", json={"audience": "watchlist", "resource_scope": {"kind": "run", "id": "run-1"}}, headers={"Authorization": f"Bearer {token}"}).json()["token"]
    assert owner.patch(f"/api/auth/members/{ids['member']}", json={"active": False}).status_code == 200
    assert introspect(member, token).status_code == 401
    assert introspect(member, grant).status_code == 401
    assert owner.patch(f"/api/auth/members/{ids['member']}", json={"active": True}).status_code == 200
    assert introspect(member, token).status_code == 401
    member = client_as("member")
    result = owner.post(f"/api/auth/members/{ids['member']}/reset")
    assert result.status_code == 200
    assert member.get("/api/auth/session").status_code == 401
    assert owner.patch(f"/api/auth/members/{ids['owner']}", json={"active": False}).status_code == 409


def test_scope_delegation_chain_service_regrant_and_revoke(identity):
    _, engine, ids = identity
    client = client_as("member")
    token = credential(client)
    response = client.post("/api/auth/delegations", json={"audience": "watchlist", "resource_scope": {"kind": "run", "id": "run-1"}}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    grant = response.json()["token"]
    principal = introspect(client, grant).json()
    assert principal["resource_scope"] == {"kind": "run", "id": "run-1"}
    assert principal["user_id"] == ids["member"]
    assert introspect(client, grant, "portfolio").status_code == 403
    body = {"audience": "portfolio", "resource_scope": {"kind": "portfolio", "id": "portfolio-a"}}
    assert client.post("/api/auth/delegations", json=body, headers={"Authorization": f"Bearer {grant}"}).status_code == 403
    service_token = "test-only-internal-service-token"
    with Session(engine) as db:
        db.add(ServiceCredential(service_id="watchlist-backend", display_name="后台", team_id="default", token_hash=token_hash(service_token), audiences=["identity"], scopes=["identity:delegate"], expires_at=now() + timedelta(hours=1)))
        db.commit()
    result = client.post("/api/auth/delegations", json=body, headers={"Authorization": f"Bearer {grant}", "X-Studio-Service-Token": service_token})
    assert result.status_code == 201, result.text
    child = result.json()["token"]
    assert introspect(client, child, "portfolio").json()["user_id"] == ids["member"]
    assert client.get("/api/auth/members", headers={"Authorization": f"Bearer {child}"}).status_code == 200
    assert client.post("/api/auth/delegations/revoke", json={"token": grant}, headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert introspect(client, child, "portfolio").status_code == 401


def test_service_identity_cannot_gain_user_admin_or_extra_audience(identity):
    _, engine, _ = identity
    service_token = "test-only-research-service"
    with Session(engine) as db:
        db.add(ServiceCredential(service_id="research", display_name="研究员", team_id="default", token_hash=token_hash(service_token), audiences=["watchlist"], scopes=["watchlist:research"], expires_at=now() + timedelta(hours=1)))
        db.commit()
    client = TestClient(app, base_url="https://testserver")
    principal = introspect(client, service_token).json()
    assert principal["kind"] == "service" and principal["user_id"] is None
    assert principal["team_role"] == "reader" and principal["is_team_owner"] is False
    assert introspect(client, service_token, "portfolio").status_code == 403
    assert client.post("/api/auth/delegations", json={"audience": "portfolio", "resource_scope": {"kind": "portfolio", "id": "secret"}}, headers={"Authorization": f"Bearer {service_token}"}).status_code == 403


def test_profile_preserves_id_and_roles_are_resolved_live(identity):
    _, _, ids = identity
    owner = client_as()
    member = client_as("member")
    token = credential(member)
    assert member.patch("/api/auth/profile", json={"display_name": "经理乙新名"}).status_code == 200
    principal = introspect(member, token).json()
    assert principal["user_id"] == ids["member"] and principal["display_name"] == "经理乙新名"
    owner.patch(f"/api/auth/members/{ids['member']}", json={"role": "reader"})
    assert introspect(member, token).json()["team_role"] == "reader"


def test_totp_enrollment_encryption_replay_and_login(identity):
    _, engine, ids = identity
    client = client_as()
    token = credential(client)
    setup = client.post("/api/auth/mfa/setup", json={"password": PASSWORD})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    otp = pyotp.TOTP(secret).now()
    assert client.post("/api/auth/mfa/confirm", json={"password": PASSWORD, "otp": otp}).status_code == 204
    assert introspect(client, token).status_code == 401
    with Session(engine) as db:
        user = db.get(User, ids["owner"])
        assert secret not in user.totp_secret
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD, "otp": otp}).status_code == 401
    with Session(engine) as db:
        # Move the used counter back to simulate the next authenticator interval.
        db.get(User, ids["owner"]).totp_last_step -= 1
        db.commit()
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD, "otp": otp}).status_code == 200


def test_password_change_revokes_all_devices(identity):
    client = client_as("member")
    second = client_as("member")
    assert client.post("/api/auth/password", json={"current_password": PASSWORD, "password": "a-new-long-password"}).status_code == 204
    assert second.get("/api/auth/session").status_code == 401


def test_initialization_is_explicit_and_bootstrap_cannot_replace_users(identity):
    _, engine, _ = identity
    with Session(engine) as db:
        with pytest.raises(ValueError, match="already contains"):
            bootstrap(db, username="replacement", display_name="替换", team_name="新团队", password_hash=hash_password(PASSWORD))


def test_expired_credentials_and_unknown_audiences_are_rejected(identity):
    _, engine, _ = identity
    client = client_as()
    token = credential(client)
    assert introspect(client, token, "unknown").status_code == 400
    with Session(engine) as db:
        db.scalar(select(SessionRecord).where(SessionRecord.token_hash == token_hash(token))).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert introspect(client, token).status_code == 401


def test_owner_transfer_is_explicit_and_audited(identity):
    _, engine, ids = identity
    member = client_as("member")
    owner = client_as()
    assert member.post("/api/auth/team/transfer", json={"user_id": ids["member"], "password": PASSWORD}).status_code == 403
    assert owner.post("/api/auth/team/transfer", json={"user_id": ids["member"], "password": PASSWORD}).status_code == 200
    assert member.get("/api/auth/session").json()["is_team_owner"]
    assert not owner.get("/api/auth/session").json()["is_team_owner"]
    assert member.patch(f"/api/auth/members/{ids['member']}", json={"role": "reader"}).status_code == 409


def test_admin_mfa_enforcement_keeps_enrollment_available(identity):
    settings, _, _ = identity
    settings.auth_require_admin_totp = True
    owner = client_as()
    assert owner.get("/api/auth/session").json()["mfa_required"]
    assert introspect(owner, credential(owner)).status_code == 403
    assert owner.post("/api/auth/members", json={"username": "blocked", "display_name": "blocked"}).status_code == 403
    assert owner.post("/api/auth/mfa/setup", json={"password": PASSWORD}).status_code == 200


def test_login_attempts_are_bounded(identity):
    client = TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 429


def test_no_runtime_legacy_identity_fallback(monkeypatch):
    from home_api.db import session
    settings = Settings(database_url="", auth_username="legacy", auth_session_secret_file="/tmp/not-used")
    monkeypatch.setattr(session, "get_settings", lambda: settings)
    client = TestClient(app)
    assert client.get("/api/auth/session").status_code == 503


def test_malformed_authorization_never_falls_back_to_cookie(identity):
    client = client_as()
    for header in ("Basic invalid", "Bearer ", ""):
        assert client.get("/api/auth/session", headers={"Authorization": header}).status_code == 401


def local_client(settings):
    settings.environment = "local"
    settings.auth_mode = "local"
    settings.frontend_url = "http://127.0.0.1:5172"
    settings.cors_origins = [settings.frontend_url]
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50100),
                      headers={"Origin": settings.frontend_url})


def test_local_owner_is_real_explicit_and_keeps_task_scope(identity):
    from studio_identity import LOCAL_OWNER_CREDENTIAL
    settings, engine, ids = identity
    client = local_client(settings)
    settings.auth_require_admin_totp = True
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    account = response.json()
    assert account["local_unrestricted"] and account["user_id"] == ids["owner"]
    assert account["is_team_owner"] and account["team_role"] == "admin" and not account["mfa_required"]
    assert "set-cookie" not in response.headers
    assert client.get("/api/auth/members").status_code == 200
    grant = client.post("/api/auth/delegations", json={"audience": "watchlist", "resource_scope": {"kind": "run", "id": "local-run"}},
                        headers={"Authorization": f"Bearer {LOCAL_OWNER_CREDENTIAL}"})
    assert grant.status_code == 201, grant.text
    token = grant.json()["token"]
    principal = introspect(client, token).json()
    assert principal["local_unrestricted"] and principal["user_id"] == ids["owner"]
    assert principal["resource_scope"] == {"kind": "run", "id": "local-run"}
    assert introspect(client, token, "portfolio").status_code == 403
    assert client.post("/api/auth/delegations", json={"audience": "watchlist", "resource_scope": {"kind": "run", "id": "other"}},
                       headers={"Authorization": f"Bearer {token}"}).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(SessionRecord)) is None
        stored = db.scalar(select(Delegation))
        assert stored.parent_kind == "local" and stored.parent_id == ids["owner"]
        db.get(User, ids["owner"]).active = False
        db.commit()
    assert introspect(client, token).status_code == 401


def test_local_mode_preserves_explicit_member_credentials_and_cloud_rejects_local(identity):
    from studio_identity import LOCAL_OWNER_CREDENTIAL
    settings, _, ids = identity
    member = client_as("member")
    member_token = credential(member)
    client = local_client(settings)
    principal = introspect(client, member_token).json()
    assert principal["user_id"] == ids["member"] and not principal.get("local_unrestricted", False)
    assert principal["team_role"] == "member"
    assert introspect(client, "invalid").status_code == 401
    assert client.get("/api/auth/session", headers={"Authorization": ""}).status_code == 401
    grant = client.post("/api/auth/delegations", json={"audience": "watchlist", "resource_scope": {"kind": "run", "id": "local-run"}},
                        headers={"Authorization": f"Bearer {LOCAL_OWNER_CREDENTIAL}"}).json()["token"]
    settings.auth_mode = "account"
    assert client.get("/api/auth/session").status_code == 401
    assert introspect(client, LOCAL_OWNER_CREDENTIAL).status_code == 401
    assert introspect(client, grant).status_code == 401
    assert introspect(client, member_token).status_code == 200


def test_local_mode_rejects_external_origin_host_and_peer(identity):
    settings, _, _ = identity
    client = local_client(settings)
    assert client.get("/api/auth/session", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/auth/session", headers={"Host": "studio.example"}).status_code == 403
    assert client.get("/api/auth/session", headers={"X-Forwarded-For": "192.168.1.20"}).status_code == 403
    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.168.1.20", 50100))
    assert remote.get("/api/auth/session").status_code == 403
    with pytest.raises(ValueError, match="environment=local"):
        Settings(environment="production", auth_mode="local")


def test_password_change_invalidates_outstanding_reset_link(identity):
    owner = client_as()
    reset = owner.post(f"/api/auth/members/{identity[2]['member']}/reset")
    token = reset.json()['activation_url'].split('#token=', 1)[1]
    member = client_as('member')
    assert member.post('/api/auth/password', json={
        'current_password': PASSWORD, 'password': 'replacement-password-long',
    }).status_code == 204
    assert owner.post('/api/auth/activate', json={'token': token, 'password': PASSWORD}).status_code == 400


def test_disable_then_restore_does_not_revive_reset_link(identity):
    owner = client_as()
    member_id = identity[2]['member']
    reset = owner.post(f'/api/auth/members/{member_id}/reset')
    token = reset.json()['activation_url'].split('#token=', 1)[1]
    for active in (False, True):
        assert owner.patch(f'/api/auth/members/{member_id}', json={'active': active}).status_code == 200
    assert owner.post('/api/auth/activate', json={'token': token, 'password': PASSWORD}).status_code == 400
