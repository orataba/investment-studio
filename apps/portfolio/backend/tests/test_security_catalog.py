from __future__ import annotations

import json
import subprocess

import pytest

from portfolio_app.api.routes import transactions
from investment_studio_instrument_core import security_catalog
from tests.conftest import REGISTRY_INSTRUMENTS


BASE = "/api/portfolios/investment-studio/securities"
PAYLOAD = {"instrument_type": "etf", "catalog_provider": "fmp", "catalog_symbol": "SHV"}
RESULT = {
    **PAYLOAD, "symbol": "SHV", "name": "iShares Short Treasury Bond ETF",
    "exchange_code": "XNAS", "exchange_label": "NASDAQ", "market": "US",
    "currency": "USD", "existing_instrument_id": None,
}


def test_search_is_read_only_and_preserves_catalog_errors(raw_client, monkeypatch):
    calls = []
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN", "portfolio-only-test-token")
    monkeypatch.setenv("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE", "/private/portfolio-test-token")
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({"results": [RESULT], "catalog_errors": {"equity": "Catalog empty"}}), "")
    monkeypatch.setattr(security_catalog.subprocess, "run", run)
    response = raw_client.get(BASE + "/search", params={"q": " SHV ", "limit": 25})
    assert response.status_code == 200
    assert response.json()["results"][0]["existing_instrument_id"] is None
    assert response.json()["catalog_errors"] == {"equity": "Catalog empty"}
    assert calls[0][0][1:] == ["data", "securities", "search", "--limit", "25", "--", "SHV"]
    assert calls[0][1]["input"] is None
    assert not any(key.startswith("INVESTMENT_STUDIO_") for key in calls[0][1]["env"])
    assert "PATH" in calls[0][1]["env"]


def test_materialize_uses_data_owner_and_returns_instrument_option(raw_client, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(REGISTRY_INSTRUMENTS[1]), "")
    monkeypatch.setattr(security_catalog.subprocess, "run", run)
    response = raw_client.post(BASE + "/materialize", json={**PAYLOAD, "catalog_symbol": " shv "})
    assert response.status_code == 200, response.text
    assert response.json()["instrument_core"]["instrument_id"] == "fund-us-agg"
    assert "source_settings" not in response.json()
    assert calls[0][0][1:] == ["data", "securities", "add", "--input", "-", "--apply"]
    assert json.loads(calls[0][1]["input"]) == {**PAYLOAD, "refresh_eod": True}


@pytest.mark.parametrize("role,status", [("viewer", 403), ("editor", 200)])
def test_materialize_requires_portfolio_editor(raw_client, monkeypatch, role, status):
    from portfolio_app.db.models import PortfolioMembershipModel
    from portfolio_app.db.session import get_session_factory
    with get_session_factory()() as session:
        session.get(PortfolioMembershipModel, ("investment-studio", "test-manager")).role = role
        session.commit()
    calls = []
    monkeypatch.setattr(transactions, "materialize_catalog_security", lambda payload: calls.append(payload) or REGISTRY_INSTRUMENTS[1])
    response = raw_client.post(BASE + "/materialize", json=PAYLOAD)
    assert response.status_code == status
    assert len(calls) == (1 if status == 200 else 0)


def test_research_delegation_cannot_materialize(raw_client, monkeypatch):
    from portfolio_app.api.authorization import authenticated_principal
    from studio_identity import Principal
    async def principal():
        return Principal("test-manager", "Test Manager", "default", resource_scope={"kind": "portfolio", "id": "investment-studio"})
    raw_client.app.dependency_overrides[authenticated_principal] = principal
    monkeypatch.setattr(transactions, "materialize_catalog_security", lambda _: pytest.fail("delegation invoked registration"))
    assert raw_client.post(BASE + "/materialize", json=PAYLOAD).status_code == 403


def test_capture_delegation_cannot_materialize(raw_client, monkeypatch):
    capture = raw_client.post("/api/portfolios/investment-studio/transaction-captures", files={"file": ("capture.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}).json()
    batch = raw_client.post("/api/portfolios/investment-studio/transaction-capture-batches", json={"capture_ids": [capture["capture_id"]], "purpose": "auto"}).json()
    monkeypatch.setattr(transactions, "materialize_catalog_security", lambda _: pytest.fail("delegation invoked registration"))
    response = raw_client.post(BASE + "/materialize", json=PAYLOAD, headers={"X-Test-Capture-Batch": batch["batch_id"]})
    assert response.status_code == 403


@pytest.mark.parametrize("payload", [{**PAYLOAD, "catalog_provider": "unknown"}, {**PAYLOAD, "instrument_type": "crypto"}, {**PAYLOAD, "catalog_symbol": " "}, {**PAYLOAD, "refresh_eod": False}])
def test_materialize_rejects_invalid_requests(raw_client, monkeypatch, payload):
    monkeypatch.setattr(transactions, "materialize_catalog_security", lambda _: pytest.fail("invalid request invoked registration"))
    assert raw_client.post(BASE + "/materialize", json=payload).status_code == 422


@pytest.mark.parametrize("code,output,status,detail", [
    (2, '{"error":"ETF is not present in the local FMP ETF catalog."}', 422, "ETF is not present"),
    (3, '{"error":"Data saved, but downstream recalculation acknowledgement failed."}', 502, "证券已登记"),
    (1, 'not json', 502, "未返回有效结果"),
])
def test_command_failures_are_visible(raw_client, monkeypatch, code, output, status, detail):
    monkeypatch.setattr(security_catalog.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, code, output, "private traceback"))
    response = raw_client.post(BASE + "/materialize", json=PAYLOAD)
    assert response.status_code == status
    assert detail in response.json()["detail"]
    assert "private traceback" not in response.text


def test_registration_timeout_explains_uncertain_write(raw_client, monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(security_catalog.subprocess, "run", timeout)
    response = raw_client.post(BASE + "/materialize", json=PAYLOAD)
    assert response.status_code == 504
    assert "登记可能已完成" in response.json()["detail"]
