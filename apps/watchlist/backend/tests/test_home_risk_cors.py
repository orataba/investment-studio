import pytest
from starlette.requests import Request
from studio_identity import IdentityError, validate_origin

from watchlist_app.core.settings import get_settings


def test_home_reads_authenticated_metadata_without_trusting_other_origins(client, monkeypatch):
    monkeypatch.delenv("INVESTMENT_STUDIO_AUTH_ALLOWED_ORIGINS", raising=False)
    allowed = get_settings().cors_origins
    for origin in ("http://127.0.0.1:5172", "http://localhost:5172"):
        response = client.get("/api/identity", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
    for origin in ("https://untrusted.example", "http://localhost:51720", "null"):
        response = client.get("/api/identity", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers
        preflight = client.options("/api/identity", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET"})
        assert preflight.status_code == 400
        request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/research/topics",
                           "server": ("127.0.0.1", 8000), "headers": [(b"origin", origin.encode())]})
        with pytest.raises(IdentityError) as failure:
            validate_origin(request, cookie_authenticated=True, allowed_origins=allowed)
        assert failure.value.status_code == 403
