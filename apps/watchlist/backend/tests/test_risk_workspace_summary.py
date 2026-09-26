from copy import deepcopy
from datetime import UTC, datetime
import json

from sqlalchemy import event

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentRiskReadModel
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory


def seed_case(session):
    case = RiskCase(case_id='summary-case', instrument_id='sxv264', signal='sector:risk-update',
        title='当前有效风险', body='完整当前风险判断，研究方向变化不能解除风控。', severity='attention',
        status='handled', trigger_active=True, follow_up_date=datetime(2026, 9, 28).date(),
        updated_at=datetime(2026, 9, 27, 8, tzinfo=UTC),
        evidence_json={'direction': 'opportunity', 'importance': 'high', 'next_watch': '继续核查融资',
            'risk_assessment': {'status': 'pending', 'reason': '此前风险仍有效'},
            'sources': [{'source_id': 'source-1', 'title': '证据原文', 'text': '原始全文' * 5000}],
            'computed': {'full_sample': list(range(3000))}},
        history_json=[{'at': '2026-09-26T08:00:00Z', 'action': 'review', 'detail': 'PM 原跟进',
            'snapshot': {'body': '旧判断保留', 'sources': [{'text': '旧原文' * 5000}]}}])
    session.add(case)
    risk = session.get(InstrumentRiskReadModel, 'sxv264')
    risk.payload_json = {**risk.payload_json, 'unused_series': ['not read in list'] * 10000}
    chart = session.get(InstrumentChartReadModel, 'sxv264')
    chart.payload_json = {**chart.payload_json, 'unused_chart': ['not read in list'] * 10000}
    session.commit()
    return deepcopy(case.evidence_json), deepcopy(case.history_json)


def test_summary_keeps_current_decision_and_reads_exact_evidence_on_demand(client):
    from tests.test_price_risk import seed
    seed(client)
    with get_session_factory()() as session:
        evidence, history = seed_case(session)
    full = client.get('/api/risk?instrument_id=sxv264').json()
    summary_response = client.get('/api/risk?instrument_id=sxv264&summary=true')
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    current = next(row for row in summary['cases'] if row['case_id'] == 'summary-case')
    original = next(row for row in full['cases'] if row['case_id'] == 'summary-case')
    for key in ('title', 'body', 'severity', 'status', 'trigger_active', 'follow_up_date', 'updated_at'):
        assert current[key] == original[key]
    assert current['evidence_json']['direction'] == 'opportunity'
    assert current['evidence_json']['risk_assessment'] == evidence['risk_assessment']
    assert current['evidence_json']['event_version_id'] == original['evidence_json']['event_version_id'] == 'summary-case:1'
    assert current['history_count'] == 1 and current['detail_available']
    assert 'history_json' not in current and 'sources' not in current['evidence_json']
    assert len(summary_response.content) < len(json.dumps(full).encode()) / 10
    for key in ('period_readings', 'drawdown_change_pp', 'previous_observation_date', 'return_kind', 'freshness'):
        assert summary['instruments'][0][key] == full['instruments'][0][key]
    assert 'unused_series' not in summary['instruments'][0]['risk']
    detail = client.get('/api/risk/cases/summary-case', params={'updated_at': current['updated_at']})
    assert detail.status_code == 200 and detail.json() == original
    assert client.get('/api/risk?instrument_ids=&summary=true').json() == {'instruments': [], 'cases': []}
    assert client.get('/api/risk/cases/not-found').status_code == 404
    with get_session_factory()() as session:
        case = session.get(RiskCase, 'summary-case')
        assert case.evidence_json == evidence and case.history_json == history
        case.updated_at = datetime(2026, 9, 27, 9, tzinfo=UTC)
        session.commit()
    assert client.get('/api/risk/cases/summary-case', params={'updated_at': current['updated_at']}).status_code == 409


def test_summary_does_not_hydrate_retained_cases_or_full_chart_risk_payloads(client):
    from tests.test_price_risk import seed
    seed(client)
    with get_session_factory()() as session:
        seed_case(session)
    loaded = []
    def record_load(session, instance):
        if isinstance(instance, (RiskCase, InstrumentChartReadModel, InstrumentRiskReadModel)):
            loaded.append(type(instance).__name__)
    from sqlalchemy.orm import Session
    event.listen(Session, 'loaded_as_persistent', record_load)
    try:
        response = client.get('/api/risk?instrument_id=sxv264&summary=true')
        assert response.status_code == 200, response.text
        assert loaded == []
    finally:
        event.remove(Session, 'loaded_as_persistent', record_load)
