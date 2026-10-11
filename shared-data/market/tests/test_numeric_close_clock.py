from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from studio_market import pipeline
from studio_market.calendars import MarketCalendarUnavailable, latest_closed_market_session
from studio_market.config import MarketSettings
from studio_market.numeric import collect as acquisition


@pytest.mark.parametrize('instant,expected', [
    ('2026-03-06T20:59:59+00:00', '2026-03-05'),
    ('2026-03-06T21:00:00+00:00', '2026-03-06'),
    ('2026-03-09T20:00:00+00:00', '2026-03-09'),
    ('2026-11-27T17:59:59+00:00', '2026-11-25'),
    ('2026-11-27T18:00:00+00:00', '2026-11-27'),
    ('2026-07-03T23:00:00+00:00', '2026-07-02'),
    ('2026-09-07T23:00:00+00:00', '2026-09-04'),
])
def test_us_cutoff_is_actual_close_including_dst_holidays_and_half_days(instant, expected):
    assert acquisition.closed_us_date(datetime.fromisoformat(instant)) == date.fromisoformat(expected)


@pytest.mark.parametrize('symbol,instant,expected', [
    ('0700.HK', '2026-10-08T16:05:00+08:00', '2026-10-07'),
    ('0700.HK', '2026-10-08T16:10:00+08:00', '2026-10-08'),
    ('0700.HK', '2026-12-24T12:05:00+08:00', '2026-12-23'),
    ('0700.HK', '2026-12-24T12:10:00+08:00', '2026-12-24'),
    ('0700.HK', '2023-09-08T19:00:00+08:00', '2023-09-07'),
    ('600519.SS', '2026-10-01T19:00:00+08:00', '2026-09-30'),
    ('000001.SZ', '2026-09-07T15:00:00+08:00', '2026-09-07'),
])
def test_symbol_cutoff_uses_confirmed_calendar_and_auction(symbol, instant, expected):
    assert acquisition.closed_symbol_date(symbol, datetime.fromisoformat(instant)) == date.fromisoformat(expected)


def test_registered_exchange_wins_over_symbol_and_unknown_is_not_guessed():
    now = datetime.fromisoformat('2026-10-08T16:30:00+08:00')
    assert acquisition.closed_symbol_date('MISLEADING', now, exchange_code='XHKG') == date(2026, 10, 8)
    assert acquisition.closed_symbol_date('MISLEADING.HK', now, exchange_code='XNYS') == date(2026, 10, 7)
    for exchange in ('UNKNOWN', ''):
        with pytest.raises(MarketCalendarUnavailable, match='No verified session calendar'):
            acquisition.closed_symbol_date('SPY', now, exchange_code=exchange)
    with pytest.raises(MarketCalendarUnavailable):
        acquisition.closed_symbol_date('UNKNOWN.EXCHANGE', now)
    with pytest.raises(ValueError, match='timezone-aware'):
        latest_closed_market_session('XNYS', datetime(2026, 10, 8))


def isolated_collector(tmp_path, monkeypatch, *, now, exchanges=None):
    store = SimpleNamespace(latest=lambda *args, **kwargs: {'rows': []})
    monkeypatch.setattr(acquisition, 'price_identities', lambda *args, **kwargs: {})
    monkeypatch.setattr(acquisition, 'price_capture_bases', lambda *args, **kwargs: {})
    monkeypatch.setattr(acquisition, 'pending_revisions', lambda *args, **kwargs: {})
    collector = acquisition.Collector(MarketSettings('sqlite://', tmp_path), store=store,
                                     now=now, exchanges=exchanges)
    calls = []
    monkeypatch.setattr(collector, '_symbol_price_capture',
                        lambda symbol, start, end, **kwargs: calls.append((symbol, start, end)))
    return collector, calls


def test_raw_direct_and_adjusted_capture_share_injected_clock_and_listing(tmp_path, monkeypatch):
    before = datetime.fromisoformat('2026-10-08T16:05:00+08:00')
    after = datetime.fromisoformat('2026-10-08T16:10:00+08:00')
    collector, calls = isolated_collector(tmp_path, monkeypatch, now=before, exchanges={'HKLISTING': 'XHKG'})
    # All default operations keep the entry instant even when wall time moves.
    class UnexpectedClock:
        @staticmethod
        def now(*args):
            pytest.fail('A capture must not fetch a fresh cutoff mid-operation')
    monkeypatch.setattr(acquisition, 'datetime', UnexpectedClock)
    collector.raw_eod(date(2026, 10, 7), date(2026, 10, 9), ['HKLISTING'])
    collector.symbol_prices(['HKLISTING'], date(2026, 10, 7), date(2026, 10, 9))
    assert [end for _, _, end in calls] == [date(2026, 10, 7)] * 2
    collector.raw_eod(date(2026, 10, 7), date(2026, 10, 9), ['OVERRIDE'],
                      now=after, exchanges={'OVERRIDE': 'XHKG'})
    assert calls[-1] == ('OVERRIDE', date(2026, 10, 7), date(2026, 10, 8))


@pytest.mark.parametrize('raw', [False, True])
def test_revision_cannot_include_unclosed_action_date(tmp_path, monkeypatch, raw):
    now = datetime.fromisoformat('2026-10-08T16:05:00+08:00')
    collector, calls = isolated_collector(tmp_path, monkeypatch, now=now, exchanges={'ALIAS': 'XHKG'})
    requested_ends = []
    def pending(*args, **kwargs):
        requested_ends.append(kwargs['end'])
        return {'ALIAS': 'revision'} if kwargs['end'] >= date(2026, 10, 8) else {}
    monkeypatch.setattr(acquisition, 'pending_revisions', pending)
    result = collector.reconcile_price_revisions(date(2026, 10, 9), symbols=['ALIAS'], raw=raw)
    assert result['status'] == 'ready'
    assert requested_ends == [date(2026, 10, 9), date(2026, 10, 7)]
    assert calls == []


def test_unknown_explicit_listing_fails_with_reason_before_capture(tmp_path, monkeypatch):
    collector, calls = isolated_collector(tmp_path, monkeypatch,
        now=datetime(2026, 10, 8, tzinfo=UTC), exchanges={'SPY': 'UNKNOWN'})
    result = collector.raw_eod(date(2026, 10, 1), date(2026, 10, 9), ['SPY'])
    assert result['status'] == 'failed'
    assert 'No verified session calendar' in result['symbols'][0]['error']
    assert calls == []


def test_pipeline_market_selection_and_capture_use_listing_and_same_instant(tmp_path, monkeypatch):
    now = datetime.fromisoformat('2026-10-08T16:10:00+08:00')
    monkeypatch.setenv('INVESTMENT_STUDIO_MARKET_ROLE', 'collector')
    monkeypatch.setattr(pipeline, 'registered_instruments', lambda settings: [
        {'symbol':'BARE', 'instrument_type':'etf', 'exchange_code':'XHKG'},
        {'symbol':'MISLEADING.HK', 'instrument_type':'equity', 'exchange_code':'XNAS'},
    ])
    calls = []
    monkeypatch.setattr(pipeline, 'collect', lambda *args, **kwargs: calls.append(kwargs) or {'status':'ready'})
    monkeypatch.setattr(pipeline, 'publish_pending', lambda *args: {'status':'ready'})
    result = pipeline.run(MarketSettings('sqlite://', tmp_path), 'registered-prices', now=now, market='hk')
    assert result['status'] == 'ready'
    assert len(calls) == 1
    assert calls[0]['symbols'] == ['BARE']
    assert calls[0]['exchanges'] == {'BARE':'XHKG'}
    assert calls[0]['now'] is now


def test_us_eod_expected_sessions_do_not_shrink_to_provider_dates(tmp_path, monkeypatch):
    collector, calls = isolated_collector(tmp_path, monkeypatch,
        now=datetime.fromisoformat('2026-10-08T20:00:00+00:00'))
    monkeypatch.setattr(collector, 'universe', lambda: {'SPY'})
    response = SimpleNamespace(payload=[{'date':'2026-10-07'}])
    collector._client = SimpleNamespace(get_json=lambda *args: response,
        get_bulk_bytes=lambda *args: pytest.fail('Incomplete provider calendar must not publish EOD'))
    archived = []
    monkeypatch.setattr(collector, 'archive', lambda value: (archived.append(value), 'raw-calendar'))
    with pytest.raises(acquisition.us_market.PriceHistoryUnavailable) as failure:
        collector.eod(date(2026, 10, 7), date(2026, 10, 8), None)
    assert failure.value.details['reason'] == 'expected_session_dates_missing'
    assert failure.value.details['missing_dates'] == ['2026-10-08']
    assert archived == [response]
    assert calls == []


@pytest.mark.parametrize('series', ['XAUUSD','DXY','BTCUSD'])
def test_market_series_utc_request_bound_does_not_claim_nyse_schedule(tmp_path, monkeypatch, series):
    # Monday was a US cash-equity holiday; this must not suppress that date for
    # series whose sessions have never been established by the NYSE calendar.
    now = datetime.fromisoformat('2026-09-08T00:10:00+00:00')
    collector, _ = isolated_collector(tmp_path, monkeypatch, now=now)
    calls = []
    collector._client = SimpleNamespace(get_json=lambda endpoint, params:
        calls.append(params) or SimpleNamespace(payload=[], received_at=now))
    monkeypatch.setattr(collector, 'archive', lambda response: ({'raw_sha256':'fixture','collected_at':now}, 'raw'))
    monkeypatch.setattr(collector, 'publish', lambda *args, **kwargs: None)
    collector.market_series(date(2026, 9, 7), date(2026, 9, 9), [series])
    assert calls[0]['to'] == '2026-09-07'
