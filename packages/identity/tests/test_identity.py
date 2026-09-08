from types import SimpleNamespace
from urllib.error import URLError
import pytest
import studio_identity as identity


def test_identity_context_is_explicit_and_restored():
    person = identity.Principal("a", "甲", "default", credential="private")
    with pytest.raises(identity.IdentityError):
        identity.current_principal()
    with identity.principal_context(person):
        assert identity.current_principal().user_id == "a"
        assert "private" not in repr(person)
        assert "credential" not in person.to_dict()
    with pytest.raises(identity.IdentityError):
        identity.current_principal()


def test_bearer_precedence_cannot_fall_back_to_cookie(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_COOKIE_NAME", "studio")
    assert identity.credential_from_headers({"cookie": "studio=session"}) == ("session", True)
    assert identity.credential_from_headers({"authorization": "Bearer run", "cookie": "studio=session"}) == ("run", False)
    with pytest.raises(identity.IdentityError):
        identity.credential_from_headers({"authorization": "Basic invalid", "cookie": "studio=session"})


def test_cookie_mutations_require_trusted_origin(monkeypatch):
    monkeypatch.delenv("INVESTMENT_STUDIO_AUTH_ALLOWED_ORIGINS", raising=False)
    request = SimpleNamespace(method="POST", headers={}, url="https://portfolio.example/api/write")
    for value in (None, "null", "https://untrusted.example"):
        request.headers = {"origin": value}
        with pytest.raises(identity.IdentityError) as caught:
            identity.validate_origin(request, cookie_authenticated=True)
        assert caught.value.status_code == 403
    request.headers = {"origin": "https://portfolio.example"}
    identity.validate_origin(request, cookie_authenticated=True)
    request.headers = {}
    identity.validate_origin(request, cookie_authenticated=False)
    request.method = "GET"
    identity.validate_origin(request, cookie_authenticated=True)


def test_unavailable_identity_never_creates_local_user(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_URL", "http://127.0.0.1:8002/api/auth")
    monkeypatch.setattr(identity, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("down")))
    with pytest.raises(identity.IdentityError) as caught:
        identity.resolve_token("present", "watchlist")
    assert caught.value.status_code == 503


def test_task_regrant_sends_backend_identity_only_to_home(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN", "backend-secret")
    monkeypatch.delenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE", raising=False)
    calls = []
    monkeypatch.setattr(identity, "_call", lambda *args, **kwargs: calls.append((args, kwargs)) or {"token": "new-task"})
    person = identity.Principal("a", "甲", "default", credential="old-task", resource_scope={"kind": "run", "id": "one"})
    assert identity.issue_delegation(person, "portfolio", {"kind": "portfolio", "id": "p1"}) == "new-task"
    assert calls[0][1]["service_token"] == "backend-secret"
    assert identity.principal_headers(person) == {"Authorization": "Bearer old-task"}


def test_service_file_permissions_and_actor_kind(monkeypatch, tmp_path):
    path = tmp_path / "service-token"
    path.write_text("service-secret")
    path.chmod(0o644)
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE", str(path))
    with pytest.raises(identity.IdentityError):
        identity.service_principal("watchlist")
    path.chmod(0o600)
    monkeypatch.setattr(identity, "resolve_token", lambda *args: identity.Principal("a", "甲", "default"))
    with pytest.raises(identity.IdentityError):
        identity.service_principal("watchlist")


def test_explicit_local_requests_resolve_owner_without_overriding_credentials(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "local")
    request = SimpleNamespace(method="GET", headers={"host": "127.0.0.1:8000"},
                              url="http://127.0.0.1:8000/api/read", client=SimpleNamespace(host="127.0.0.1"))
    def introspection(path, token, payload):
        return identity.Principal("owner" if token == identity.LOCAL_OWNER_CREDENTIAL else "member", "姓名", "default",
            team_role="admin" if token == identity.LOCAL_OWNER_CREDENTIAL else "reader",
            local_unrestricted=token == identity.LOCAL_OWNER_CREDENTIAL,
            resource_scope={"kind": "run", "id": "bound"} if token == "bound" else None).to_dict()
    monkeypatch.setattr(identity, "_call", introspection)
    assert identity.resolve_request(request, "watchlist").local_unrestricted
    request.headers["authorization"] = "Bearer bound"
    principal = identity.resolve_request(request, "watchlist")
    assert principal.user_id == "member" and not principal.local_unrestricted
    assert principal.resource_scope == {"kind": "run", "id": "bound"}
    request.headers["authorization"] = ""
    with pytest.raises(identity.IdentityError):
        identity.resolve_request(request, "watchlist")


@pytest.mark.parametrize("peer,host,origin", [
    ("192.168.1.2", "127.0.0.1", None), ("127.0.0.1", "portfolio.example", None),
    ("127.0.0.1", "127.0.0.1", "https://attacker.example"),
])
def test_local_owner_rejects_remote_peers_and_browser_destinations(monkeypatch, peer, host, origin):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "local")
    request = SimpleNamespace(method="GET", headers={"host": host, "origin": origin},
                              url=f"http://{host}/api/read", client=SimpleNamespace(host=peer))
    with pytest.raises(identity.IdentityError) as caught:
        identity.resolve_request(request, "watchlist")
    assert caught.value.status_code == 403


def test_cloud_never_accepts_missing_credentials_or_a_local_owner_response(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_MODE", "account")
    request = SimpleNamespace(method="GET", headers={}, url="http://127.0.0.1/api/read", client=SimpleNamespace(host="127.0.0.1"))
    with pytest.raises(identity.IdentityError) as caught:
        identity.resolve_request(request, "watchlist")
    assert caught.value.status_code == 401
    monkeypatch.setattr(identity, "_call", lambda *args: identity.Principal("owner", "shaw", "default", local_unrestricted=True).to_dict())
    with pytest.raises(identity.IdentityError) as caught:
        identity.resolve_token(identity.LOCAL_OWNER_CREDENTIAL, "watchlist")
    assert caught.value.status_code == 403
