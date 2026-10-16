from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import event, select

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentRiskReadModel
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.workbench import RiskReviewRule
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.canonical_recalc import _current_drawdown
from watchlist_app.services.price_risk import period_loss_readings
from watchlist_app.services.risk_workspace_price_context import build_workspace_price_context, apply_workspace_price_context
from .test_price_risk import seed, series
from .test_risk_workspace_summary import seed_case


def without_monitoring(value):
    value = deepcopy(value)
    for asset in value['instruments']:
        if asset.get('risk'):
            asset['risk'].pop('monitoring_summary', None)
    return value


@pytest.mark.parametrize('kind', ['daily', 'weekly', 'missing', 'segmented', '24/7', 'empty'])
@pytest.mark.parametrize('limits', [{}, {'day': 0, 'week': 2, 'quarter': 10}])
def test_published_context_matches_direct_price_rules_for_each_sample_boundary(kind, limits):
    source = series([100 * .99 ** i for i in range(100)])
    if kind == 'weekly':
        source['frequency']['resolved_frequency'] = 'weekly'
    elif kind == 'missing':
        source['intervals'][-1].update(status='unavailable', value=None, reason='missing_market_session')
    elif kind == 'segmented':
        for point in source['points'][-10:]:
            point['segment_id'] = 'new-confirmed-segment'
    elif kind == '24/7':
        source['frequency']['gap_detection_basis'] = 'market_calendar:24/7'
    elif kind == 'empty':
        source = {}
    risk = {'current_drawdown': -30, 'data_quality': {'status': 'ready'}}
    context = build_workspace_price_context(source, risk)
    before = deepcopy(context)
    asset = {'period_limits': limits}
    apply_workspace_price_context(asset, context)
    assert asset['period_readings'] == period_loss_readings(source, limits)
    assert context == before  # A new rule does not mutate this metric generation.
    assert context['minimum_returns'] == (90 if kind == '24/7' else 63)
    if source.get('points'):
        previous = _current_drawdown([{**p, 'as_of_date': date.fromisoformat(p['date'])} for p in source['points'][:-1]])
        assert asset['drawdown_change_pp'] == -30 - previous
        assert asset['previous_observation_date'] == source['points'][-2]['date']
    else:
        assert asset['drawdown_change_pp'] is asset['previous_observation_date'] is None


def test_workspace_projection_preserves_all_consumed_fields_cases_and_live_rules(client, monkeypatch):
    seed(client)
    with get_session_factory()() as session:
        seed_case(session)
        chart = session.get(InstrumentChartReadModel, 'sxv264')
        risk = session.get(InstrumentRiskReadModel, 'sxv264')
        context = deepcopy(risk.workspace_price_context_json)
        assert context == build_workspace_price_context(chart.payload_json['research_returns'], risk.payload_json)
        assert risk.last_recalculated_at == chart.last_recalculated_at
        assert risk.source_cutoff_at == chart.source_cutoff_at
    path = '/api/risk?instrument_id=sxv264&summary=true'
    expected = without_monitoring(client.get(path).json())
    from watchlist_app.services import risk_workspace_projection
    original = risk_workspace_projection.return_series
    monkeypatch.setattr(risk_workspace_projection, 'return_series', lambda *_: pytest.fail('workspace read full return history'))
    result = client.get(path + '&projection=workspace')
    assert result.status_code == 200 and result.json() == expected
    case = next(row for row in result.json()['cases'] if row['case_id'] == 'summary-case')
    assert client.get('/api/risk/cases/summary-case', params={'updated_at': case['updated_at']}).json()['history_json']
    assert client.get('/api/risk?instrument_ids=&projection=workspace').json() == {'instruments': [], 'cases': []}
    assert client.get('/api/risk?projection=unsupported').status_code == 422
    # A rule may be edited independently of the captured market generation.
    with get_session_factory()() as session:
        rule = session.get(RiskReviewRule, 'sxv264')
        if rule is None:
            rule = RiskReviewRule(instrument_id='sxv264', calibration_json={})
            session.add(rule)
        rule.period_limits_json = {'day': 0, 'quarter': 5}
        rule.calibration_json = {**rule.calibration_json, 'rule_revision': 12, 'severities': {'day': 'severe'}}
        session.commit()
    updated = client.get(path + '&projection=workspace').json()
    monkeypatch.setattr(risk_workspace_projection, 'return_series', original)
    assert updated == without_monitoring(client.get(path).json())
    assert updated['instruments'][0]['rule_version'] == 'risk-rule:sxv264:12'
    with get_session_factory()() as session:
        assert session.get(InstrumentRiskReadModel, 'sxv264').workspace_price_context_json == context


def test_missing_legacy_context_is_read_only_and_keeps_the_same_contract(client):
    seed(client)
    with get_session_factory()() as session:
        session.get(InstrumentRiskReadModel, 'sxv264').workspace_price_context_json = None
        session.commit()
        jobs = list(session.scalars(select(RecalcJob.recalc_job_id)))
    path = '/api/risk?instrument_id=sxv264&summary=true'
    expected = without_monitoring(client.get(path).json())
    writes = []
    def capture(_conn, _cursor, statement, *_):
        if statement.lstrip().split()[0].upper() in {'UPDATE', 'INSERT', 'DELETE'}:
            writes.append(statement)
    event.listen(get_engine(), 'before_cursor_execute', capture)
    try:
        assert client.get(path + '&projection=workspace').json() == expected
    finally:
        event.remove(get_engine(), 'before_cursor_execute', capture)
    assert writes == []
    with get_session_factory()() as session:
        assert session.get(InstrumentRiskReadModel, 'sxv264').workspace_price_context_json is None
        assert list(session.scalars(select(RecalcJob.recalc_job_id))) == jobs


def test_inactive_asset_keeps_retained_cases_without_reading_or_repairing_prices(client, monkeypatch):
    seed(client)
    from watchlist_app.db.models import InstrumentDetail
    with get_session_factory()() as session:
        seed_case(session)
        session.get(InstrumentDetail, 'sxv264').is_active = False
        session.get(InstrumentRiskReadModel, 'sxv264').workspace_price_context_json = None
        session.commit()
    path = '/api/risk?instrument_id=sxv264&summary=true'
    expected = client.get(path).json()
    assert expected['instruments'] == [] and expected['cases']
    from watchlist_app.services import risk_workspace_projection
    monkeypatch.setattr(risk_workspace_projection, 'return_series', lambda *_: pytest.fail('inactive price read'))
    assert client.get(path + '&projection=workspace').json() == expected
