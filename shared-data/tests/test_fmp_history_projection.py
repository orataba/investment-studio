"""A local history replay must not mix source versions or publish truncation."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from studio_data.services.fmp import crypto, eod


@pytest.fixture(params=["listed", "crypto"])
def projection(request, monkeypatch):
    module = eod if request.param == "listed" else crypto
    symbol = "REVIEW" if module is eod else "BTCUSD"
    instrument = {
        "instrument_type": "equity" if module is eod else "crypto",
        "currency": "USD",
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": f"fmp:{symbol}"}],
    }
    publish = Mock(return_value=(1, 1))
    status = Mock(side_effect=lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(module, "upsert_price_history", publish)
    monkeypatch.setattr(module, "update_refresh_status", status)

    def refresh(store):
        if module is eod:
            return eod.refresh_fmp_eod("review", instrument_type="equity", store=store)
        return crypto.refresh_fmp_crypto_eod("review", store=store, now=datetime(2026, 9, 8, tzinfo=UTC))

    return module, symbol, refresh, publish, status


def test_one_file_view_keeps_history_consistent_during_source_revision(projection, tmp_path, monkeypatch):
    module, symbol, refresh, publish, _ = projection
    dataset = "raw_eod_daily" if module is eod else "market_series_daily"
    revised_field = "adjusted_close" if module is eod else "close"
    start = date(2026, 9, 7) - timedelta(days=10000)
    rows = [dict(date=(start + timedelta(days=index)).isoformat(), symbol=symbol, series_id=symbol,
                 open=100, high=110, low=40, close=100, adjusted_close=100) for index in range(10001)]
    source = eod.NumericStore(eod.MarketSettings(f"sqlite:///{tmp_path / 'numeric.db'}", tmp_path / "numeric"))
    source.create_schema_for_testing()
    try:
        source.ingest(dataset, [rows], source="fixture", observed_at=datetime(2026, 9, 7, tzinfo=UTC))
        query = source.query
        revised = False

        def query_during_revision(*args, **kwargs):
            nonlocal revised
            result = query(*args, **kwargs)
            if not revised:
                revised = True
                # Publish after the first read, exactly where a paged reader
                # would switch its remaining observations to another version.
                source.ingest(dataset, [[{**row, revised_field: 50} for row in rows]], source="fixture",
                              observed_at=datetime(2026, 9, 8, tzinfo=UTC))
            return result

        monkeypatch.setattr(source, "query", query_during_revision)
        for expected in (Decimal("100"), Decimal("50")):
            refresh(source)
            payload = publish.call_args.kwargs
            basis = "adjusted_close" if module is eod else "close"
            points = [row for row in payload["market_data_rows"] if row["quote_basis"] == basis]
            assert len(points) == len(payload["price_bar_rows"]) == 10001
            assert {row["value"] for row in points} == {expected}
            if module is eod:
                assert {row["adjustment_factor"] for row in payload["price_bar_rows"]} == {expected / 100}
            else:
                assert {row["close"] for row in payload["price_bar_rows"]} == {expected}
        assert query(dataset, versions=True)["total"] == 20002
    finally:
        source.close()


@pytest.mark.parametrize("total", [1, 100001])
def test_incomplete_history_never_publishes_a_partial_result(projection, total):
    _, symbol, refresh, publish, status = projection
    row = dict(date="2026-09-07", symbol=symbol, series_id=symbol,
               open=100, high=110, low=90, close=100, adjusted_close=100)
    source = Mock()
    source.query.return_value = {"rows": [] if total == 1 else [row], "total": total}
    with pytest.raises(ValueError, match="incomplete history"):
        refresh(source)
    publish.assert_not_called()
    status.assert_not_called()
    source.query.assert_called_once()
