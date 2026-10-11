from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from studio_market.futu import FutuOAuth, FutuOAuthError
from studio_market.futu import oauth


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.closed = False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def token_response(**overrides):
    return {"access_token": "private-access", "refresh_token": "private-refresh",
            "token_type": "Bearer", "scope": "quote:read", "expires_in": 7200, **overrides}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def write_private(path, payload):
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


def seed_client(directory, *, port=60355, expires_at=None):
    directory.mkdir(mode=0o700)
    write_private(directory / "client.json", {
        "client_id": "test-client", "redirect_uri": f"http://127.0.0.1:{port}/callback",
        "scope": "quote:read",
    })
    if expires_at is not None:
        write_private(directory / "tokens.json", {
            "access_token": "old-access", "refresh_token": "old-refresh",
            "expires_at": expires_at, "scope": "quote:read",
        })


def callback_action(callbacks):
    """Serve real loopback callbacks while OAuth's main thread waits for them."""
    pool = ThreadPoolExecutor(max_workers=1)
    futures = []
    seen = []

    def action(url):
        seen.append(url)
        query = parse_qs(urlsplit(url).query)

        def visit():
            session = requests.Session()
            session.trust_env = False
            try:
                return [session.get(query["redirect_uri"][0], params=make(query), timeout=2).status_code
                        for make in callbacks]
            finally:
                session.close()

        futures.append(pool.submit(visit))

    return action, seen, futures, pool


def valid_callback(query):
    return {"state": query["state"][0], "code": "private-code"}


def test_unconfigured_status_does_not_create_directory_or_request(tmp_path):
    directory = tmp_path / "not-created"
    session = Session()
    manager = FutuOAuth(directory, session=session)
    assert manager.status() == {
        "configured": False, "authenticated": False, "scope": None,
        "expires_at": None, "needs_refresh": False,
    }
    assert not directory.exists()
    assert session.calls == []


def test_registration_requests_only_quote_scope_and_discards_management_credentials(tmp_path):
    directory = tmp_path / "secrets" / "futu"
    uri = "http://127.0.0.1:60355/callback"
    response = Response({"client_id": "test-client", "redirect_uris": [uri],
                         "scope": "quote:read trade:write", "registration_access_token": "private-management"})
    session = Session(response)
    manager = FutuOAuth(directory, session=session)
    result = manager.setup()
    assert result["configured"] is True and result["authenticated"] is False
    assert manager.setup() == result
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == "https://webapi.futunn.com/oauth2/register"
    assert request["json"]["scope"] == "quote:read"
    assert request["json"]["token_endpoint_auth_method"] == "none"
    assert request["allow_redirects"] is False
    assert json.loads((directory / "client.json").read_text()) == {
        "client_id": "test-client", "redirect_uri": uri, "scope": "quote:read",
    }
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(directory.parent.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    assert response.closed
    with pytest.raises(FutuOAuthError, match="different callback port"):
        manager.setup(port=60356)


def test_authorization_checks_state_pkce_and_keeps_codes_and_tokens_out_of_output(tmp_path, capsys):
    directory = tmp_path / "futu"
    seed_client(directory, port=free_port())
    session = Session(Response(token_response()))
    manager = FutuOAuth(directory, session=session, clock=lambda: 1000)
    action, urls, futures, pool = callback_action([
        lambda query: {"state": "wrong", "code": "wrong-code"},
        lambda query: [("state", query["state"][0]), ("state", query["state"][0]), ("code", "duplicate-state")],
        valid_callback,
    ])
    with pool:
        result = manager.authorize(on_authorization_url=action, timeout_seconds=3)
        assert futures[0].result() == [400, 400, 200]
    assert result["authenticated"] is True and result["scope"] == "quote:read"
    assert result["needs_refresh"] is False
    query = parse_qs(urlsplit(urls[0]).query)
    assert urls[0].startswith("https://webapi.futunn.com/oauth2/authorize/confirm?")
    assert query["scope"] == ["quote:read"] and query["code_challenge_method"] == ["S256"]
    url, request = session.calls[0]
    assert url == "https://webapi.futunn.com/oauth2/token"
    assert request["data"]["code"] == "private-code"
    assert request["allow_redirects"] is False
    challenge = base64.urlsafe_b64encode(hashlib.sha256(request["data"]["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == [challenge]
    tokens = json.loads((directory / "tokens.json").read_text())
    assert tokens["access_token"] == "private-access"
    assert tokens["expires_at"] == 8200
    assert stat.S_IMODE((directory / "tokens.json").stat().st_mode) == 0o600
    captured = capsys.readouterr()
    safe_output = repr(result) + captured.out + captured.err
    for secret in ("private-access", "private-refresh", "private-code", request["data"]["code_verifier"]):
        assert secret not in safe_output
    assert "private-code" not in "".join(path.read_text() for path in directory.iterdir())


@pytest.mark.parametrize("scope", [None, "", "quote:read trade:write", "quote:read accid:123", "quote:write"])
def test_initial_authorization_refuses_missing_or_expanded_scope_without_persisting(tmp_path, scope):
    directory = tmp_path / "futu"
    seed_client(directory, port=free_port())
    payload = token_response(scope=scope)
    if scope is None:
        del payload["scope"]
    manager = FutuOAuth(directory, session=Session(Response(payload)))
    action, _, futures, pool = callback_action([valid_callback])
    with pool, pytest.raises(FutuOAuthError, match="only quote:read"):
        manager.authorize(on_authorization_url=action, timeout_seconds=3)
    assert futures[0].result() == [200]
    assert not (directory / "tokens.json").exists()
    assert manager.status()["authenticated"] is False


def test_denial_and_timeout_never_exchange_a_code(tmp_path):
    directory = tmp_path / "futu"
    seed_client(directory, port=free_port())
    session = Session()
    manager = FutuOAuth(directory, session=session)
    action, _, futures, pool = callback_action([
        lambda query: {"state": query["state"][0], "error": "access_denied", "error_description": "private-response"},
    ])
    with pool, pytest.raises(FutuOAuthError, match="denied or cancelled") as captured:
        manager.authorize(on_authorization_url=action, timeout_seconds=3)
    assert futures[0].result() == [400]
    assert "private-response" not in str(captured.value)
    with pytest.raises(FutuOAuthError, match="timed out"):
        manager.authorize(on_authorization_url=lambda _: None, timeout_seconds=0.03)
    assert not session.calls and not (directory / "tokens.json").exists()


def test_refresh_retains_unrotated_refresh_token_and_status_is_secret_free(tmp_path):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=100)
    payload = token_response(access_token="new-access")
    del payload["refresh_token"]
    session = Session(Response(payload))
    manager = FutuOAuth(directory, session=session, clock=lambda: 1000)
    assert manager.status()["needs_refresh"] is True
    assert manager.access_token() == "new-access"
    assert manager.access_token() == "new-access"
    assert len(session.calls) == 1
    request = session.calls[0][1]
    assert request["data"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh",
                               "client_id": "test-client", "scope": "quote:read"}
    assert request["allow_redirects"] is False
    saved = json.loads((directory / "tokens.json").read_text())
    assert saved["refresh_token"] == "old-refresh"
    result = manager.status()
    assert result["needs_refresh"] is False
    assert "new-access" not in str(result) and "old-refresh" not in str(result)


@pytest.mark.parametrize("scope", [None, "quote:read trade:read", "quote:read quote:write"])
def test_refresh_requires_explicit_exact_scope_and_retains_previous_file(tmp_path, scope):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=100)
    original = (directory / "tokens.json").read_bytes()
    payload = token_response(scope=scope)
    if scope is None:
        del payload["scope"]
    manager = FutuOAuth(directory, session=Session(Response(payload)), clock=lambda: 1000)
    with pytest.raises(FutuOAuthError, match="only quote:read"):
        manager.access_token()
    assert (directory / "tokens.json").read_bytes() == original


def test_concurrent_expired_readers_reload_under_lock_and_refresh_once(tmp_path):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=100)
    entered, release = threading.Event(), threading.Event()

    class BlockingSession(Session):
        def post(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super().post(*args, **kwargs)

    session = BlockingSession(Response(token_response()))
    first = FutuOAuth(directory, session=session, clock=lambda: 1000)
    second = FutuOAuth(directory, session=session, clock=lambda: 1000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first.access_token)
        assert entered.wait(timeout=3)
        two = pool.submit(second.access_token)
        release.set()
        assert one.result(timeout=3) == two.result(timeout=3) == "private-access"
    assert len(session.calls) == 1


def test_force_refresh_after_401_uses_an_already_refreshed_file(tmp_path):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=8000)
    session = Session(Response(token_response(access_token="other-process-access")))
    first = FutuOAuth(directory, session=session, clock=lambda: 1000)
    second = FutuOAuth(directory, session=session, clock=lambda: 1000)
    assert first.access_token() == "old-access"
    assert second.access_token(force_refresh=True) == "other-process-access"
    assert first.access_token(force_refresh=True) == "other-process-access"
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [
    Response({"access_token": "private-value"}, status=302),
    Response({"error_description": "private-value"}, status=400),
    Response(ValueError("private-value")),
    Response({"error": "private-value", "error_description": "private-value"}),
    requests.ConnectionError("private-value"),
])
def test_http_failures_are_sanitized_and_redirects_are_not_followed(tmp_path, response):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=100)
    session = Session(response)
    manager = FutuOAuth(directory, session=session, clock=lambda: 1000)
    with pytest.raises(FutuOAuthError) as captured:
        manager.access_token()
    assert "private-value" not in str(captured.value)
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False
    assert json.loads((directory / "tokens.json").read_text())["access_token"] == "old-access"


def test_rejects_checkout_and_insecure_or_linked_credential_storage(tmp_path):
    with pytest.raises(FutuOAuthError, match="outside the repository"):
        FutuOAuth(Path(__file__).resolve().parents[1] / "private-futu")
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=8000)
    manager = FutuOAuth(directory, session=Session())
    directory.chmod(0o755)
    with pytest.raises(FutuOAuthError, match="0700"):
        manager.status()
    directory.chmod(0o700)
    token_file = directory / "tokens.json"
    token_file.chmod(0o644)
    with pytest.raises(FutuOAuthError, match="0600"):
        manager.access_token()
    token_file.chmod(0o600)
    os.link(token_file, directory / "linked-token")
    with pytest.raises(FutuOAuthError, match="private regular files"):
        manager.access_token()
    token_file.unlink()
    token_file.symlink_to(directory / "linked-token")
    with pytest.raises(FutuOAuthError, match="private Futu OAuth file"):
        manager.access_token()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(FutuOAuthError, match="symlink"):
        FutuOAuth(alias)


def test_failed_atomic_replace_preserves_previous_credentials_and_cleans_temporary_file(tmp_path, monkeypatch):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=100)
    original = (directory / "tokens.json").read_bytes()
    manager = FutuOAuth(directory, session=Session(Response(token_response())), clock=lambda: 1000)

    def failed_replace(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(oauth.os, "replace", failed_replace)
    with pytest.raises(OSError, match="storage unavailable"):
        manager.access_token()
    assert (directory / "tokens.json").read_bytes() == original
    assert set(path.name for path in directory.iterdir()) == {"client.json", "tokens.json", ".oauth.lock"}


def test_saved_grant_scope_is_rechecked_before_use(tmp_path):
    directory = tmp_path / "futu"
    seed_client(directory, expires_at=8000)
    tokens = json.loads((directory / "tokens.json").read_text())
    tokens["scope"] = "quote:read trade:write"
    write_private(directory / "tokens.json", tokens)
    session = Session()
    manager = FutuOAuth(directory, session=session, clock=lambda: 1000)
    with pytest.raises(FutuOAuthError, match="only quote:read"):
        manager.access_token()
    assert session.calls == []
