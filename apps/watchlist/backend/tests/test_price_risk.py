from datetime import date, timedelta

import pytest

from watchlist_app.services.price_risk import initial_price_limits, period_loss_readings
from watchlist_app.services.calculation_frequency import _market_calendar_sessions
from investment_studio_instrument_core import build_return_intervals


def series(values, **frequency):
    start = date(2026, 1, 2)
    dates = _market_calendar_sessions('XNYS', start, start + timedelta(days=len(values) * 2 + 20))
    assert dates is not None and len(dates) >= len(values)
    points = [{'date': d.isoformat(), 'value': v} for d, v in zip(dates, values)]
    return {
        'metadata': {'return_kind': 'total_return', 'return_series_status': 'ready'},
        'frequency': {'resolved_frequency': 'daily', 'gap_count': 0, 'gap_detection_basis': 'market_calendar:XNYS', **frequency},
        'points': points,
        'intervals': build_return_intervals(points),
    }


def test_periods_use_actual_compounded_endpoints_and_ignore_small_moves():
    source = series([100 * .99 ** i for i in range(80)])
    readings = period_loss_readings(source, {'day': 2, 'week': 4, 'month': 10, 'quarter': 20})
    assert [r['observations'] for r in readings] == [1, 5, 21, 63]
    for row in readings:
        assert row['return_pct'] == pytest.approx((.99 ** row['observations'] - 1) * 100)
        assert row['start_date'] == source['points'][-row['observations'] - 1]['date']
    assert [r['breached'] for r in readings] == [False, True, True, True]
    tiny = period_loss_readings(series([100] * 79 + [99.9]), {'day': .5, 'week': 1, 'month': 2, 'quarter': 3})
    assert not any(r['breached'] for r in tiny)


def test_initial_lines_reflect_volatility_with_documented_minimum_tolerances():
    assert initial_price_limits(series([100] * 80))[0] == {'day': .5, 'week': 1, 'month': 2, 'quarter': 3}
    low, _ = initial_price_limits(series([100 * (1.01 if i % 2 else 1) for i in range(300)]))
    high, calibration = initial_price_limits(series([100 * (1.04 if i % 2 else 1) for i in range(300)]))
    assert all(high[key] > low[key] for key in low)
    assert calibration['observations'] == 252
    assert all(value * 2 == int(value * 2) for value in high.values())
    assert initial_price_limits(series([100] * 63)) == ({}, {})


def test_later_anchor_is_usable_but_unconfirmed_return_break_is_not():
    source = series([100] * 80)
    source['metadata'].update(return_series_status='partial', return_segment_breaks=[])
    assert initial_price_limits(source)[0]['day'] == .5
    source['metadata']['return_segment_breaks'] = [{'as_of_date': '2026-05-01', 'reason': 'unconfirmed'}]
    assert initial_price_limits(source) == ({}, {})
    assert all(row['return_pct'] is None for row in period_loss_readings(source, {'day': 1}))


@pytest.mark.parametrize('frequency', [{'gap_count': 1}, {'resolved_frequency': 'weekly'}])
def test_missing_or_low_frequency_prices_are_not_treated_as_daily(frequency):
    source = series([100] * 79 + [50], **frequency)
    if frequency.get('gap_count'):
        source['intervals'][-1].update(status='unavailable', value=None, log_return=None, reason='missing_market_session')
    assert initial_price_limits(source) == ({}, {})
    readings = period_loss_readings(source, {'day': 1, 'week': 1, 'month': 1, 'quarter': 1})
    assert all(row['return_pct'] is None and row['limitation'] and not row['breached'] for row in readings)


def seed(client):
    wid = client.post("/api/watchlists", json={"name": "Price risk test"}).json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["sxv264"]}).status_code == 200
    from watchlist_app.services.recalc_worker import drain_recalc_jobs
    drain_recalc_jobs()


def observe(source, *, fresh=True):
    from watchlist_app.db.models import InstrumentChartReadModel, InstrumentRiskReadModel, InstrumentSummaryReadModel
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, 'sxv264')
        chart.payload_json = {'research_returns': source}
        chart.data_freshness_status = 'fresh' if fresh else 'stale'
        risk = session.get(InstrumentRiskReadModel, 'sxv264')
        risk.payload_json = {'current_drawdown': -1, 'data_quality': {'status': 'ready'}}
        risk.data_freshness_status = 'fresh' if fresh else 'stale'
        summary = session.get(InstrumentSummaryReadModel, 'sxv264')
        summary.payload_json = {'freshness': {'data_freshness_status': risk.data_freshness_status, 'latest_observation_date': source['points'][-1]['date']}}
        refresh_risk_cases(session, ['sxv264'])
        session.commit()


def workspace(client):
    return client.get('/api/risk?instrument_id=sxv264').json()


def test_initialized_lines_stay_fixed_and_explicit_disable_is_preserved(client):
    seed(client)
    observe(series([100] * 80), fresh=False)
    assert not workspace(client)['instruments'][0]['period_limits']
    observe(series([100] * 80))
    initial = workspace(client)['instruments'][0]
    observe(series([100 * (1.05 if i % 2 else 1) for i in range(80)]))
    assert workspace(client)['instruments'][0]['period_limits'] == initial['period_limits']
    assert client.put('/api/risk/rules/sxv264', json={'drawdown_limit': 10}).status_code == 200
    assert client.put('/api/risk/rules/sxv264', json={'period_limits': dict.fromkeys(['day', 'week', 'month', 'quarter'])}).status_code == 200
    observe(series([100] * 80))
    result = workspace(client)['instruments'][0]
    assert all(value is None for value in result['period_limits'].values())
    assert result['drawdown_limit'] == 10
    assert client.put('/api/risk/rules/sxv264', json={'period_limits': {'day': -1}}).status_code == 422


def test_period_breaches_share_one_case_escalate_and_do_not_resolve_on_missing_data(client):
    seed(client)
    observe(series([100] * 80))
    client.put('/api/risk/rules/sxv264', json={'period_limits': {'day': .5, 'week': 10, 'month': 10, 'quarter': 10}})
    def active():
        return [case for case in workspace(client)['cases'] if case['signal'] == 'period_loss' and case['trigger_active']]
    observe(series([100] * 79 + [99]))
    case = active()[0]
    assert [row['period'] for row in case['evidence_json']['periods']] == ['day']
    client.put(f"/api/risk/cases/{case['case_id']}", json={'status': 'handled', 'note': '已复核单日下跌'})
    observe(series([100] * 79 + [88]))
    escalated = active()
    assert len(escalated) == 1 and escalated[0]['case_id'] == case['case_id']
    assert escalated[0]['status'] == 'open'
    assert len(escalated[0]['evidence_json']['periods']) == 4
    history_size = len(escalated[0]['history_json'])
    observe(series([100] * 79 + [87]))
    assert len(active()[0]['history_json']) == history_size
    missing = series([100] * 80, gap_count=1)
    missing['intervals'][-1].update(status='unavailable', value=None, log_return=None, reason='missing_market_session')
    observe(missing)
    assert len(active()) == 1
    observe(series([100] * 80), fresh=False)
    assert len(active()) == 1
    observe(series([100] * 80))
    assert active() == []
    observe(series([100] * 79 + [88]))
    assert active()[0]['case_id'] != case['case_id']
    observe(series([100] * 80), fresh=False)
    client.put('/api/risk/rules/sxv264', json={'period_limits': dict.fromkeys(['day', 'week', 'month', 'quarter'])})
    assert active() == []


def test_period_settings_migration_preserves_existing_drawdown_lines(tmp_path, monkeypatch):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from watchlist_app.core.settings import get_settings
    database_url = f'sqlite+pysqlite:///{tmp_path / "price-risk-migration.db"}'
    monkeypatch.setenv('INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL', database_url)
    monkeypatch.setenv('INVESTMENT_STUDIO_WATCHLIST_DATABASE_SCHEMA', '')
    get_settings.cache_clear()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE risk_review_rule (instrument_id TEXT PRIMARY KEY, drawdown_limit FLOAT)'))
        connection.execute(text("INSERT INTO risk_review_rule VALUES ('fund', 12.5)"))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    command.stamp(config, '20260905_0053')
    command.upgrade(config, '20260905_0054')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT drawdown_limit, period_limits_json, calibration_json FROM risk_review_rule')).one() == (12.5, '{}', '{}')
    command.downgrade(config, '20260905_0053')
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT drawdown_limit FROM risk_review_rule')) == 12.5
    get_settings.cache_clear()


def test_old_return_reset_does_not_disable_confirmed_recent_loss_windows():
    source = series([100] * 70 + [1] * 79 + [.95])
    source['metadata'].update(return_series_status='partial', return_segment_breaks=[{}])
    for i, point in enumerate(source['points']):
        point['segment_id'] = 'old' if i < 70 else 'new'
    limits, calibration = initial_price_limits(source)
    assert limits
    assert calibration['observations'] == 79
    assert calibration['sample_start'] == source['points'][70]['date']
    assert period_loss_readings(source, {'day': 3})[0]['return_pct'] == pytest.approx(-5)
    # A fresh reset has only a one-day window, not an invented quarter loss.
    for point in source['points'][-2:]:
        point['segment_id'] = 'newest'
    assert initial_price_limits(source) == ({}, {})
    readings = period_loss_readings(source, {'day': 3, 'quarter': 3})
    assert readings[0]['breached']
    assert readings[-1]['return_pct'] is None


def test_confirmed_recent_loss_is_monitored_even_when_full_history_drawdown_is_unavailable(client):
    seed(client)
    source = series([100] * 70 + [1] * 79 + [.95])
    source['metadata'].update(return_series_status='partial', return_segment_breaks=[{}])
    for i, point in enumerate(source['points']):
        point['segment_id'] = 'old' if i < 70 else 'new'
    observe(source)
    from watchlist_app.db.models import InstrumentRiskReadModel
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    with get_session_factory()() as session:
        risk = session.get(InstrumentRiskReadModel, 'sxv264')
        risk.payload_json = {'current_drawdown': None, 'data_quality': {'status': 'unavailable'}}
        refresh_risk_cases(session, ['sxv264'])
        session.commit()
    body = workspace(client)
    asset = body['instruments'][0]
    assert asset['price_rule_summary']['counts']['triggered'] > 0
    assert any(row['signal'] == 'period_loss' for row in body['cases'])


def test_pending_chart_recalculation_cannot_trigger_new_recent_loss(client):
    seed(client)
    source = series([100] * 79 + [95])
    from watchlist_app.db.models import InstrumentChartReadModel, InstrumentRiskReadModel, InstrumentSummaryReadModel
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, 'sxv264')
        chart.payload_json = {'research_returns': source}
        chart.data_freshness_status = 'pending_recalc'
        risk = session.get(InstrumentRiskReadModel, 'sxv264')
        risk.payload_json = {'current_drawdown': None, 'data_quality': {'status': 'unavailable'}}
        summary = session.get(InstrumentSummaryReadModel, 'sxv264')
        summary.payload_json = {'freshness': {'data_freshness_status': 'fresh', 'latest_observation_date': source['points'][-1]['date']}}
        refresh_risk_cases(session, ['sxv264'])
        session.commit()
        from watchlist_app.db.models.workbench import RiskCase
        from sqlalchemy import select
        assert not list(session.scalars(select(RiskCase).where(RiskCase.instrument_id == 'sxv264', RiskCase.signal == 'period_loss', RiskCase.trigger_active.is_(True))))


@pytest.mark.parametrize('missing_index,calibration_available,week_available', [(10, True, True), (-3, False, False)])
def test_only_gaps_inside_the_actual_loss_or_calibration_sample_disable_it(missing_index, calibration_available, week_available):
    source = series([100] * 399 + [95], gap_count=1, unavailable_return_interval_count=1)
    missing = source['points'].pop(missing_index)['date']
    source['intervals'] = build_return_intervals(source['points'], break_dates=[missing])
    limits, calibration = initial_price_limits(source)
    assert bool(limits) is calibration_available
    if calibration_available:
        assert calibration['observations'] == 252
    readings = {row['period']: row for row in period_loss_readings(source, {'day': 3, 'week': 3, 'month': 3, 'quarter': 3})}
    # The last single-day interval is complete in both cases.
    assert readings['day']['return_pct'] == pytest.approx(-5)
    assert readings['day']['breached']
    assert (readings['week']['return_pct'] is not None) is week_available
    if not week_available:
        assert readings['week']['limitation'] and not readings['week']['breached']
    # Missing interval evidence cannot be substituted with zero or inferred from levels.
    source['intervals'].pop()
    assert period_loss_readings(source, {'day': 3})[0]['return_pct'] is None


@pytest.mark.parametrize('missing_evidence', ['intervals', 'calendar'])
def test_zero_aggregate_gaps_cannot_certify_daily_price_risk(missing_evidence):
    source = series([100] * 79 + [95])
    assert source['frequency']['gap_count'] == 0
    if missing_evidence == 'intervals':
        source.pop('intervals')
    else:
        source['frequency']['gap_detection_basis'] = 'calendar_day_threshold'
    assert initial_price_limits(source) == ({}, {})
    readings = period_loss_readings(source, {'day': 3, 'week': 3})
    assert all(row['return_pct'] is None and row['limitation'] and not row['breached'] for row in readings)
