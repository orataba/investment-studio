from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.providers.hkex import daily_quotations_url
from studio_data.services.fmp import eod_capture
from studio_data.services.fmp.client import FmpApiError


NOW = datetime(2026, 10, 6, 10, tzinfo=UTC)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    settings = MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path/'objects')
    store = NumericStore(settings)
    store.create_schema_for_testing()
    store.ingest('raw_eod_daily', [[dict(symbol='2100.HK', date='2026-09-30',
        open=.25, high=.265, low=.25, close=.265, adjusted_close=.265, volume=102000)]], source='fixture')
    monkeypatch.setattr(eod_capture.MarketSettings, 'from_environment', lambda: settings)
    statuses = []
    monkeypatch.setattr(eod_capture, 'update_refresh_status', lambda **kw: statuses.append(kw))
    collector = Mock()
    collector.raw_eod.return_value = {'status': 'ready'}
    yield store, collector, statuses
    store.close()


def publish(store, day, *, status='no_trade', close='0.265', currency='HKD', symbol='2100.HK'):
    store.ingest('hkex_security_sessions', [[dict(symbol=symbol, date=day,
        session_status=status, official_close=close, currency=currency, source_url=daily_quotations_url(day))]],
        source='hkex', observed_at=NOW, raw_ref='numeric/raw/hkex/test.gz')


def run(capture):
    store, collector, _ = capture
    return eod_capture.ensure_fmp_security_history('2100-hk', symbol='2100.HK', exchange_code='XHKG',
        currency='HKD', store=store, collector=collector, now=NOW)


def test_every_missing_tail_session_needs_proof_and_prices_are_unchanged(capture, monkeypatch):
    store, collector, statuses = capture
    before = store.query('raw_eod_daily')['rows']
    days = []
    def report(market, day):
        days.append(day)
        publish(market, day)
    monkeypatch.setattr(eod_capture, 'ensure_session_report', report)
    evidence = run(capture)
    assert days == [date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)]
    assert {row['date'] for row in evidence} == {'2026-10-02', '2026-10-05', '2026-10-06'}
    assert store.query('raw_eod_daily')['rows'] == before
    assert statuses == []
    collector.raw_eod.assert_called_once()


@pytest.mark.parametrize('mismatch', [
    {'status': 'traded'}, {'status': 'suspended'}, {'status': 'unknown'},
    {'currency': 'USD'}, {'close': '0.27'}, {'symbol': '2101.HK'},
])
def test_missing_or_inconsistent_daily_evidence_still_fails(capture, monkeypatch, mismatch):
    def report(store, day):
        publish(store, day, **(mismatch if day == date(2026, 10, 5) else {}))
    monkeypatch.setattr(eod_capture, 'ensure_session_report', report)
    with pytest.raises(FmpApiError, match='not ready'):
        run(capture)
    assert capture[2][-1]['status'] == 'failed'


def test_download_failure_and_pending_adjustment_are_not_hidden(capture, monkeypatch):
    monkeypatch.setattr(eod_capture, 'ensure_session_report', Mock(side_effect=RuntimeError('private transport detail')))
    with pytest.raises(FmpApiError, match='RuntimeError') as error:
        run(capture)
    assert 'private transport detail' not in str(error.value)
    store, _, _ = capture
    store.ingest('dividends', [], source='fixture', details={
        'price_revision_requests': [{'symbol': '2100.HK', 'effective_date': '2026-10-02'}]})
    with pytest.raises(FmpApiError, match='pending adjustment revisions'):
        run(capture)
