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
