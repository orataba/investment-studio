import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core.db_models import Instrument, InstrumentMarketData
from studio_data.services.fmp import eod
from test_fmp_identity_projection import CLOCK, projection


@pytest.fixture
def release(projection, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/reconcile_release_fmp_prices.py'
    spec = importlib.util.spec_from_file_location('release_fmp_reconciliation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'get_session_factory', lambda: projection.factory)
    monkeypatch.setattr(module, 'NumericStore', lambda _: projection.market)
    return module


def scope(tmp_path, ids, clock=CLOCK.isoformat()):
    path = tmp_path / 'scope.json'
    path.write_text(json.dumps({'clock': clock, 'instrument_ids': ids}))
    return path


def test_reviewed_offline_replay_withdraws_pollution_and_preserves_source(release, projection, tmp_path, monkeypatch):
    p = projection
    p.price([('2026-10-04', 90), ('2026-10-09', 100)])
    # Reproduce the prior canonical behavior, including an off-session bar.
    with monkeypatch.context() as old:
        old.setattr(eod, 'closed_market_sessions', lambda *a: [CLOCK.date().replace(day=4), CLOCK.date().replace(day=9)])
        p.refresh()
    source_before = p.market.query('raw_eod_daily', versions=True)
    assert len(core.get_price_bars(p.factory, instrument_id=p.iid)) == 2
    result = release.reconcile(scope(tmp_path, [p.iid]))
    assert result['offline'] is True and result['completed'][0]['instrument_id'] == p.iid
    assert [str(row['date']) for row in core.get_price_bars(p.factory, instrument_id=p.iid)] == ['2026-10-09']
    assert all(row['status'] == 'unavailable' for row in p.load()['market_data'] if str(row['as_of_date']) == '2026-10-04')
    assert p.market.query('raw_eod_daily', versions=True) == source_before
    repeated = release.reconcile(scope(tmp_path, [p.iid]))
    assert repeated['completed'][0]['status'] == 'no_new_data'


@pytest.mark.parametrize('change', ['other_owner', 'other_type', 'mixed_provider'])
def test_review_checks_current_ownership_before_any_replay(release, projection, tmp_path, monkeypatch, change):
    p = projection
    p.price([('2026-10-09', 100)])
    p.refresh()
    with p.factory() as session:
        instrument = session.get(Instrument, p.iid)
        if change == 'other_owner':
            instrument.source_settings_json = {'source_api_profile': 'futu'}
        elif change == 'other_type':
            instrument.instrument_type = 'crypto'
            instrument.exchange_code = None
        else:
            session.scalar(select(InstrumentMarketData).where(InstrumentMarketData.instrument_id == p.iid)).provider = 'manual'
        session.commit()
    monkeypatch.setattr(release, 'refresh_fmp_eod', lambda *a, **kw: pytest.fail('Invalid scope reached a writer'))
    with pytest.raises(ValueError):
        release.reconcile(scope(tmp_path, [p.iid]))


@pytest.mark.parametrize('ids,clock', [([], CLOCK.isoformat()), (['a', 'a'], CLOCK.isoformat()),
    (['a'], '2026-10-11T00:00:00'), (['a'], '2026-10-11T00:00:00+08:00')])
def test_scope_requires_unique_ids_and_fixed_utc_clock(release, tmp_path, ids, clock):
    with pytest.raises(ValueError):
        release.read_scope(scope(tmp_path, ids, clock))


def test_missing_history_is_not_a_successful_release_repair(release, projection, tmp_path):
    with pytest.raises(RuntimeError, match='full history'):
        release.reconcile(scope(tmp_path, [projection.iid]))
