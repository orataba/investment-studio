from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from home_api.core.settings import Settings
from home_api.main import app


def _private_file(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def _password_hash(password: str) -> str:
    salt = b"test-auth-salt"
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"scrypt$16384$8$1${encoded_salt}${encoded_digest}"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_username="yungu",
        auth_password_hash_file=_private_file(
            tmp_path / "password-hash", _password_hash("yungu")
        ),
        auth_session_secret_file=_private_file(
            tmp_path / "session-secret", "a" * 64
        ),
        auth_cookie_domain=None,
    )


def test_login_session_and_logout(monkeypatch, tmp_path: Path) -> None:
    from home_api.api.routes import auth

    monkeypatch.setattr(auth, "get_settings", lambda: _settings(tmp_path))
    client = TestClient(app, base_url="https://testserver")

    login_response = client.post(
        "/api/auth/login",
        json={"username": "yungu", "password": "yungu"},
    )
    assert login_response.status_code == 200
    assert login_response.json() == {"authenticated": True, "username": "yungu"}
    assert "HttpOnly" in login_response.headers["set-cookie"]
    assert "Secure" in login_response.headers["set-cookie"]
    assert "SameSite=strict" in login_response.headers["set-cookie"]

    assert client.get("/api/auth/check").status_code == 204
    assert client.get("/api/auth/session").json() == {
        "authenticated": True,
        "username": "yungu",
    }

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/check").status_code == 401


def test_login_rejects_invalid_credentials(monkeypatch, tmp_path: Path) -> None:
    from home_api.api.routes import auth

    monkeypatch.setattr(auth, "get_settings", lambda: _settings(tmp_path))
    response = TestClient(app, base_url="https://testserver").post(
        "/api/auth/login",
        json={"username": "yungu", "password": "wrong"},
    )

    assert response.status_code == 401
