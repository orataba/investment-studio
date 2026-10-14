from io import BytesIO
import json
from urllib.error import HTTPError, URLError
import pytest
from fastapi import HTTPException
from studio_identity import Principal, principal_context
from portfolio_app.api.routes import instrument_risk


@pytest.fixture(autouse=True)
def request_identity():
    with principal_context(Principal("test-manager", "Test Manager", "default", credential="test-session")):
        yield


def test_risk_bridge_keeps_scope_and_forwards_followup_to_same_store(monkeypatch):
    calls = []
    def request(req, timeout):
        calls.append(req)
        return BytesIO(json.dumps({'case_id': 'original', 'status': 'investigating'}).encode())
    monkeypatch.setattr(instrument_risk, 'urlopen', request)
    instrument_risk.workspace('')
    assert calls[-1].full_url.endswith('/api/risk?instrument_ids=')
    instrument_risk.workspace('fund-a,fund-b')
    assert calls[-1].full_url.endswith('instrument_ids=fund-a%2Cfund-b')
    result = instrument_risk.follow_up('original', {'status': 'investigating', 'note': '已联系管理人'})
    assert result['case_id'] == 'original'
    assert calls[-1].get_header('Authorization') == 'Bearer test-session'
    assert calls[-1].method == 'PUT'
    assert calls[-1].full_url.endswith('/risk/cases/original')
    assert json.loads(calls[-1].data)['note'] == '已联系管理人'


def test_risk_bridge_preserves_validation_and_reports_unavailable(monkeypatch):
    def invalid(req, timeout):
        raise HTTPError(req.full_url, 422, 'Invalid', {}, BytesIO(b'{"detail":"invalid rule"}'))
    monkeypatch.setattr(instrument_risk, 'urlopen', invalid)
    with pytest.raises(HTTPException) as error:
        instrument_risk.set_rule('fund', {'drawdown_limit': -1})
    assert error.value.status_code == 422
    def offline(req, timeout):
        raise URLError('unavailable')
    monkeypatch.setattr(instrument_risk, 'urlopen', offline)
    with pytest.raises(HTTPException) as error:
        instrument_risk.workspace('fund')
    assert error.value.status_code == 503


def test_risk_bridge_forwards_summary_and_versioned_detail_under_same_actor(monkeypatch):
    calls = []
    def request(req, timeout):
        calls.append(req)
        return BytesIO(b'{}')
    monkeypatch.setattr(instrument_risk, 'urlopen', request)
    instrument_risk.workspace('fund-a', summary=True)
    assert calls[-1].full_url.endswith('/api/risk?instrument_ids=fund-a&summary=true')
    instrument_risk.case_detail('shared-case', updated_at='2026-09-27T08:00:00+00:00')
    assert calls[-1].full_url.endswith('/api/risk/cases/shared-case?updated_at=2026-09-27T08%3A00%3A00%2B00%3A00')
    assert calls[-1].get_header('Authorization') == 'Bearer test-session'


@pytest.mark.parametrize("method,path,payload", [
    ("PATCH", "/api/instrument-risk/notices/run-one/notice-one", {"expected_notice_version": 1, "level": "medium", "title": "更正", "body": "组合编辑者意见", "reason": "事实更新"}),
    ("PUT", "/api/instrument-risk/notices/receipt", {"notice_id": "notice-one", "notice_version": 1, "run_id": "run-one", "action": "read"}),
])
def test_notice_actions_forward_team_reader_with_original_identity(raw_client, monkeypatch, method, path, payload):
    from portfolio_app.api.authorization import authenticated_principal
    reader = Principal("portfolio-editor", "Portfolio Editor", "default", team_role="reader", credential="reader-session")
    raw_client.app.dependency_overrides[authenticated_principal] = lambda: reader
    calls = []

    def request(req, timeout):
        calls.append(req)
        return BytesIO(b'{"accepted": true}')

    monkeypatch.setattr(instrument_risk, "urlopen", request)
    response = raw_client.request(method, path, json=payload)
    assert response.status_code == 200, response.text
    assert calls[-1].method == method
    assert calls[-1].full_url.endswith(path.replace("/api/instrument-risk", "/api/risk"))
    assert calls[-1].get_header("Authorization") == "Bearer reader-session"
    assert json.loads(calls[-1].data) == payload

    def denied(req, timeout):
        raise HTTPError(req.full_url, 403, "Forbidden", {}, BytesIO(b'{"detail":"notice scope denied"}'))

    monkeypatch.setattr(instrument_risk, "urlopen", denied)
    response = raw_client.request(method, path, json=payload)
    assert response.status_code == 403
    assert response.json()["detail"] == "notice scope denied"
    # The exception is limited to notice actions; shared rule writes stay denied.
    response = raw_client.put("/api/instrument-risk/rules/fund", json={"drawdown_limit": -0.2})
    assert response.status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize("principal", [
    Principal(None, "Service", "default", kind="service", service_id="service", local_unrestricted=True),
    Principal("editor", "Editor", "default", resource_scope={"kind": "portfolio", "id": "investment-studio"}),
    Principal("editor", "Editor", "default", resource_scope={"kind": "capture", "id": "capture-one"}),
])
@pytest.mark.parametrize("method,path", [
    ("PATCH", "/api/instrument-risk/notices/run-one/notice-one"),
    ("PUT", "/api/instrument-risk/notices/receipt"),
])
def test_notice_actions_reject_service_and_delegation_before_forwarding(raw_client, monkeypatch, principal, method, path):
    from portfolio_app.api.authorization import authenticated_principal
    raw_client.app.dependency_overrides[authenticated_principal] = lambda: principal

    def unexpected_forward(*args, **kwargs):
        raise AssertionError("notice action must not reach Watchlist")

    monkeypatch.setattr(instrument_risk, "urlopen", unexpected_forward)
    assert raw_client.request(method, path, json={}).status_code == 403
