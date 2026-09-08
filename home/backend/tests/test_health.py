import os
import subprocess
import sys

from fastapi.testclient import TestClient

from home_api.core.settings import Settings
from home_api.main import app


def test_home_owns_identity_without_business_data_or_maintenance_dependency():
    assert "database_url" in Settings.model_fields
    assert not any("fmp" in name or "email" in name for name in Settings.model_fields)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("INVESTMENT_STUDIO_")}
    subprocess.run([
        sys.executable, "-c",
        "import sys; from home_api.main import app; "
        "assert not any(name.split('.')[0] in {'studio_data', 'investment_studio_instrument_core'} for name in sys.modules)",
    ], env=environment, check=True)


def test_health_only_describes_home():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "app", "environment"}


def test_http_has_no_data_management_surface():
    client = TestClient(app)
    for path in ("/api/instruments", "/api/securities/search", "/api/dashboard", "/api/fx-rates"):
        assert client.get(path).status_code == 404
