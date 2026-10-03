from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
import gzip

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric import hkex_sessions
from studio_market.numeric.providers.hkex import HkexQuotationError, daily_quotations_url, parse_daily_quotations
from studio_market.numeric.providers.http_client import PublicResponse


DAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 3, 8, tzinfo=UTC)
# Representative rows and exact columns from HKEX's 2026-10-02 daily report.
REPORT = b'''<html><pre>DATE: 02 OCT 2026 (FRIDAY)
<a name = "quotations">QUOTATIONS</a>
 CODE  NAME OF STOCK    CUR PRV.CLO./    ASK/    HIGH/      SHARES TRADED/
                            CLOSING      BID     LOW        TURNOVER ($)
*    5 HSBC HOLDINGS    HKD  158.00   149.60   151.20           24,964,374
                             149.50   149.50   148.80        3,741,272,467
    33 INTL GENIUS      HKD TRADING SUSPENDED
    92#CHAMPION TECH    HKD TRADING HALTED
  1687 PRC B2710        USD     N/A      -        -                      -
                                N/A      -        -                      -
  2099 CHINAGOLDINTL    HKD  220.40   217.00   221.60              751,046
                             217.00   216.80   213.00          161,714,928
  2100 BAIOO            HKD    0.265    0.27      -                      -
                               0.265    0.255     -                      -
  2101 FULU HOLDINGS    HKD    0.935    0.935    0.915               6,000
                               0.915    0.915    0.915               5,550
<a name = "sales_all">SALES RECORDS FOR ALL STOCKS</a>
</pre></html>'''


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path/'market.db'}", tmp_path/'objects'))
    result.create_schema_for_testing()
    yield result
    result.close()


def test_official_table_separates_no_trade_from_halts_and_missing_fields():
    rows = {row['symbol']: row for row in parse_daily_quotations(REPORT, expected_date=DAY)}
    assert rows['2100.HK']['session_status'] == 'no_trade'
    assert rows['2100.HK']['official_close'] == '0.265'
    assert rows['2100.HK']['shares_traded'] is None
    assert rows['2099.HK']['session_status'] == 'traded'
    assert rows['0005.HK']['shares_traded'] == '24964374'
    assert rows['0033.HK']['session_status'] == rows['0092.HK']['session_status'] == 'suspended'
    assert rows['1687.HK']['session_status'] == 'unknown'
    assert not {'open', 'high', 'low', 'adjusted_close'} & rows['2100.HK'].keys()


@pytest.mark.parametrize('body', [
    REPORT.replace(b'02 OCT 2026', b'01 OCT 2026'),
    REPORT.split(b'<a name = "sales_all">')[0],
    REPORT.replace(b'PRV.CLO./', b'NEW.COLUMNS'),
    REPORT.replace(b'0.265    0.255     -                      -', b'0.265    0.255'),
    REPORT.replace(b'  2101 FULU', b'  2100 FULU'),
    b'<html>temporarily unavailable</html>',
])
def test_wrong_date_partial_or_invalid_reports_cannot_certify_no_trade(body):
    with pytest.raises(HkexQuotationError):
        parse_daily_quotations(body, expected_date=DAY)


def test_capture_is_shared_by_concurrent_symbols_and_keeps_source_clock(store, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return PublicResponse(url, {}, NOW, REPORT)
    monkeypatch.setattr(hkex_sessions, 'get_public_bytes', fetch)
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: hkex_sessions.ensure_session_report(store, DAY), range(2)))
    assert calls == [daily_quotations_url(DAY)]
    rows = hkex_sessions.read_no_trade_evidence(store, symbol='2100.HK', currency='HKD',
        last_close='0.265', after=date(2026, 9, 30), through=DAY, as_of=NOW)
    assert len(rows) == 1
    assert rows[0]['observed_at'] == NOW.isoformat()
    assert gzip.decompress((store.settings.data_root/rows[0]['raw_ref']).read_bytes()) == REPORT
    assert store.query('raw_eod_daily')['total'] == 0
    assert hkex_sessions.read_no_trade_evidence(store, symbol='2100.HK', currency='HKD',
        last_close='0.265', after=date(2026, 9, 30), through=DAY,
        as_of=datetime(2026, 10, 2, 10, tzinfo=UTC)) == []
    for currency, close in [('USD', '0.265'), ('HKD', '0.26')]:
        assert hkex_sessions.read_no_trade_evidence(store, symbol='2100.HK', currency=currency,
            last_close=close, after=date(2026, 9, 30), through=DAY) == []
